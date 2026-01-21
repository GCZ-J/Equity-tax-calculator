# 兜底：自动安装缺失的依赖
import subprocess
import sys
def install_package(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

try:
    import plotly.express as px
except ImportError:
    install_package("plotly>=5.18.0")
    import plotly.express as px

try:
    import xlsxwriter
except ImportError:
    install_package("xlsxwriter>=3.1.9")
    import xlsxwriter

try:
    import openpyxl
except ImportError:
    install_package("openpyxl>=3.1.2")
    import openpyxl

# 核心库导入
import streamlit as st
import pandas as pd
import io
import math

# ---------------------- 页面基础配置 ----------------------
st.set_page_config(
    page_title="股权激励计税工具",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ---------------------- 核心规则配置 ----------------------
# 1. 激励工具规则（含期权/RS/RSU/SAR）
INCENTIVE_TOOLS = {
    "期权": {
        "type": "行权类",
        "income_formula": "行权收入 =（行权日市价 - 行权价）× 行权数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    },
    "限制性股票(RS)": {
        "type": "归属类",
        "income_formula": "归属收入 = 归属日市价 × 归属数量 - 授予价 × 归属数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    },
    "限制性股票单位(RSU)": {
        "type": "归属类",
        "income_formula": "归属收入 = 归属日市价 × 归属数量（无授予价）",
        "income_calc": lambda ep, mp, q, *args: mp * q
    },
    "股票增值权(SAR)": {
        "type": "现金结算类",
        "income_formula": "结算收入 =（行权日市价 - 授予价）× 行权数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    }
}

# 2. 行权/归属方式规则
EXERCISE_METHODS = {
    "现金行权/归属": {
        "desc": "现金支付行权价/授予价，全额持有股票",
        "actual_quantity": lambda q, tax, ep, mp: q,
        "formula": "实际持有数量=行权/归属数量"
    },
    "卖股/净股缴税": {
        "desc": "卖出部分股票支付税款，剩余持有（RSU默认此方式）",
        "actual_quantity": lambda q, tax, ep, mp: q - math.ceil(tax / (mp or 1)),
        "formula": "实际持有数量=行权/归属数量 - 向上取整(税款÷市价)"
    },
    "无现金行权": {
        "desc": "券商垫付行权价，卖出部分股票偿还（仅适用于期权）",
        "actual_quantity": lambda q, tax, ep, mp: q - math.ceil((ep*q + tax) / (mp or 1)),
        "formula": "实际持有数量=行权数量 - 向上取整((行权总价+税款)÷市价)"
    },
    "现金结算": {
        "desc": "不获取股票，直接领取现金差价（仅适用于SAR/RSU）",
        "actual_quantity": lambda q, tax, ep, mp: 0,
        "formula": "实际持有数量=0（现金结算）"
    }
}

# 3. 转让类型规则
TRANSFER_TYPES = {
    "无转让": {"fee_rate": 0.0, "desc": "未转让股票，无费用/税款"},
    "二级市场卖出": {"fee_rate": 0.0015, "desc": "普通交易，含佣金/印花税（默认0.15%）"},
    "大宗交易": {"fee_rate": 0.003, "desc": "大额转让，费用率更高（默认0.3%）"}
}

# 4. 税务规则（修正税率表，明确速算扣除数逻辑）
TAX_RULES = {
    "中国大陆": {
        # 格式：(上限, 税率, 速算扣除数)
        "annual_brackets": [
            (36000, 0.03, 0),
            (144000, 0.1, 2520),
            (300000, 0.2, 16920),
            (420000, 0.25, 31920),
            (660000, 0.3, 52920),
            (960000, 0.35, 85920),
            (float('inf'), 0.45, 181920)
        ],
        "transfer_tax_rate": 0.2,  # 财产转让所得税率20%
        "transfer_tax_exempt": {
            "境内": True,  # 境内上市公司股票转让暂免
            "境外": False  # 港美股等境外上市需缴税
        },
        "policy_basis": "财政部 税务总局公告2023年第25号"
    },
    "中国香港": {
        "annual_brackets": [
            (50000, 0.02, 0), (50000, 0.06, 1000), (50000, 0.1, 3000),
            (50000, 0.14, 5000), (float('inf'), 0.17, 7000)
        ],
        "transfer_tax_rate": 0.0,  # 香港无资本利得税
        "transfer_tax_exempt": {"境内": True, "境外": True},
        "policy_basis": "香港税务局《税务条例》"
    },
    "新加坡": {
        "annual_brackets": [
            (20000, 0.02, 0), (10000, 0.035, 400), (10000, 0.07, 750),
            (40000, 0.115, 1150), (40000, 0.15, 2750), (40000, 0.18, 4750),
            (40000, 0.19, 6550), (40000, 0.2, 8150), (float('inf'), 0.22, 8950)
        ],
        "transfer_tax_rate": 0.0,  # 新加坡无资本利得税
        "transfer_tax_exempt": {"境内": True, "境外": True},
        "policy_basis": "新加坡税务局IRAS规定"
    }
}

# ---------------------- 条件格式化函数 ----------------------
def highlight_tax_cell(val, threshold):
    """浅色背景下，税款超过阈值时标浅灰"""
    GRAY_COLOR = "#f0f0f0"
    if isinstance(val, (int, float)) and val > threshold:
        return f"background-color: {GRAY_COLOR}"
    return ""

def apply_tax_highlight(df, tax_columns, threshold):
    """对指定税款列应用格式化"""
    return df.style.applymap(
        lambda val: highlight_tax_cell(val, threshold),
        subset=tax_columns
    ).hide(axis="index")

# ---------------------- 修正后的税率计算函数（核心！） ----------------------
def calculate_single_tax(income, brackets):
    """
    正确的超额累进税率计算逻辑：
    1. 找到应纳税所得额对应的税率档位
    2. 应纳税额 = 应纳税所得额 × 该档位税率 - 该档位速算扣除数
    """
    income = max(income, 0.0)  # 收入不能为负
    tax = 0.0
    
    # 找到适用的档位
    applicable_rate = 0.0
    applicable_deduction = 0.0
    for upper, rate, deduction in brackets:
        if income <= upper:
            applicable_rate = rate
            applicable_deduction = deduction
            break
        # 最后一档（无上限）
        if upper == float('inf'):
            applicable_rate = rate
            applicable_deduction = deduction
            break
    
    # 核心修正：用速算扣除数计算总税款，而非逐档累加
    tax = income * applicable_rate - applicable_deduction
    tax = max(tax, 0.0)  # 税款不能为负
    return round(tax, 2)

# ---------------------- 核心计算函数（含单条净收益+准确抵税股） ----------------------
def calculate_single_record(record, tax_resident, is_listed, listing_location):
    # 基础字段（容错取值）
    record_id = record["id"]
    incentive_tool = record["incentive_tool"]
    exercise_method = record["exercise_method"]
    transfer_type = record.get("transfer_type", "无转让")
    ep = record["exercise_price"]  # 行权价/授予价
    eq = record["exercise_quantity"]  # 行权/归属数量
    mp = record["exercise_market_price"]  # 行权/归属日市价
    tp = record.get("transfer_price", 0.0)  # 转让价
    transfer_fee_rate = record.get("transfer_fee_rate", 0.0)  # 转让费用率

    # 1. 计算行权/归属收入
    exercise_income = INCENTIVE_TOOLS[incentive_tool]["income_calc"](ep, mp, eq)
    exercise_income = max(exercise_income, 0.0)

    # 2. 计算行权/归属税款（使用修正后的函数）
    rule = TAX_RULES[tax_resident]
    single_tax = calculate_single_tax(exercise_income, rule["annual_brackets"])

    # 3. 计算实际持股+抵税股出售+剩余到账股数（基于准确税款）
    actual_qty = EXERCISE_METHODS[exercise_method]["actual_quantity"](eq, single_tax, ep, mp)
    actual_qty = max(actual_qty, 0)
    tax_shares = 0  # 抵税股出售数量
    remaining_shares = 0  # 剩余到账股数

    if exercise_method != "现金结算":
        if exercise_method == "卖股/净股缴税":
            # 抵税股 = 税款 ÷ 市价（向上取整，确保税款足额缴纳）
            tax_shares = math.ceil(single_tax / (mp or 1))
            tax_shares = max(tax_shares, 0)
            remaining_shares = eq - tax_shares
            remaining_shares = max(remaining_shares, 0)
        elif exercise_method == "无现金行权":
            # 抵税股 = (行权总价+税款) ÷ 市价（向上取整）
            tax_shares = math.ceil((ep*eq + single_tax) / (mp or 1))
            tax_shares = max(tax_shares, 0)
            remaining_shares = eq - tax_shares
            remaining_shares = max(remaining_shares, 0)
        else:
            tax_shares = "——"
            remaining_shares = "——"
    else:
        tax_shares = "——"
        remaining_shares = "——"

    # 4. 计算转让相关
    transfer_fee = 0.0
    transfer_income = 0.0
    transfer_tax = 0.0
    if actual_qty > 0 and tp > 0 and transfer_type != "无转让":
        gross_transfer_income = tp * actual_qty  # 转让总收入
        transfer_fee = round(gross_transfer_income * transfer_fee_rate, 2)  # 转让费用
        transfer_income = round(gross_transfer_income - transfer_fee - (mp * actual_qty), 2)  # 转让净收入
        transfer_income = max(transfer_income, 0.0)
        # 转让税款
        if tax_resident == "中国大陆":
            exempt = rule["transfer_tax_exempt"].get(listing_location, False)
        else:
            exempt = rule["transfer_tax_exempt"]["境外"]
        if not exempt and transfer_income > 0:
            transfer_tax = round(transfer_income * rule["transfer_tax_rate"], 2)

    # 5. 计算转让净收益 + 单条记录净收益
    transfer_net = round(transfer_income - transfer_tax - transfer_fee, 2)
    single_record_net = round(exercise_income - single_tax + transfer_net, 2)

    return {
        "记录ID": record_id,
        "激励工具类型": incentive_tool,
        "行权/归属方式": exercise_method,
        "转让类型": transfer_type,
        "行权/授予价(元/股)": ep,
        "行权/归属数量(股)": eq,
        "行权/归属日市价(元/股)": mp,
        "转让价(元/股)": tp,
        "转让费用率(%)": round(transfer_fee_rate * 100, 2),
        "行权/归属收入(元)": exercise_income,
        "行权/归属税款(元)": single_tax,
        "抵税股出售数量(股)": tax_shares,
        "剩余到账股数(股)": remaining_shares,
        "实际持有数量(股)": actual_qty,
        "转让费用(元)": transfer_fee,
        "转让收入(元)": transfer_income,
        "转让税款(元)": transfer_tax,
        "转让净收益(元)": transfer_net,
        "单条记录净收益(元)": single_record_net
    }

# ---------------------- 年度汇总函数 ----------------------
def calculate_yearly_consolidation(detail_results, tax_resident, is_listed, listing_location, other_income, special_deduction):
    rule = TAX_RULES[tax_resident]
    # 基础指标合计
    total_exercise_income = sum([r["行权/归属收入(元)"] for r in detail_results])
    total_exercise_tax = sum([r["行权/归属税款(元)"] for r in detail_results])
    total_transfer_income = sum([r["转让收入(元)"] for r in detail_results])
    total_transfer_fee = sum([r["转让费用(元)"] for r in detail_results])
    total_transfer_tax = sum([r["转让税款(元)"] for r in detail_results])
    total_transfer_net = sum([r["转让净收益(元)"] for r in detail_results])
    total_single_net = sum([r["单条记录净收益(元)"] for r in detail_results])
    # 年度总抵税股出售数量
    total_tax_shares = 0
    for r in detail_results:
        ts = r["抵税股出售数量(股)"]
        if isinstance(ts, int):
            total_tax_shares += ts

    # 中国大陆特殊计税规则
    if tax_resident == "中国大陆":
        if is_listed:
            # 上市公司单独计税（重新计算合计税款，确保准确）
            total_exercise_tax = calculate_single_tax(total_exercise_income, rule["annual_brackets"])
            tax_desc = "上市公司股权激励单独计税（工资薪金所得）"
        else:
            # 非上市公司并入综合所得
            taxable_income = max(total_exercise_income + other_income - 60000 - special_deduction, 0.0)
            total_exercise_tax = calculate_single_tax(taxable_income, rule["annual_brackets"])
            tax_desc = "非上市公司股权激励并入综合所得计税"
    else:
        tax_desc = f"{tax_resident} 当地规则计税（行权/归属收入单独计税）"

    # 年度核心指标
    total_yearly_tax = round(total_exercise_tax + total_transfer_tax, 2)
    total_yearly_income = round(total_exercise_income + total_transfer_income, 2)
    net_income = round(total_single_net, 2)

    return {
        "税务居民身份": tax_resident,
        "是否上市公司": "是" if is_listed else "否",
        "上市地": listing_location,
        "年度行权/归属总收入(元)": total_exercise_income,
        "年度行权/归属总税款(元)": total_exercise_tax,
        "年度总抵税股出售数量(股)": total_tax_shares,
        "年度转让总收入(元)": total_transfer_income,
        "年度转让总费用(元)": total_transfer_fee,
        "年度转让总税款(元)": total_transfer_tax,
        "年度转让净收益(元)": total_transfer_net,
        "年度单条净收益合计(元)": total_single_net,
        "年度总税款(元)": total_yearly_tax,
        "年度总收益(元)": total_yearly_income,
        "年度净收益(元)": net_income,
        "计税规则说明": tax_desc
    }

# ---------------------- 报税表单生成函数 ----------------------
def generate_tax_form(yearly_result, detail_results, tax_resident):
    rule = TAX_RULES[tax_resident]
    form_data_list = []
    for r in detail_results:
        form_data = {
            "记录ID": r["记录ID"],
            "激励工具类型": r["激励工具类型"],
            "行权/归属方式": r["行权/归属方式"],
            "转让类型": r["转让类型"],
            "行权/归属收入(元)": r["行权/归属收入(元)"],
            "行权/归属税款(元)": r["行权/归属税款(元)"],
            "抵税股出售数量(股)": r["抵税股出售数量(股)"],
            "剩余到账股数(股)": r["剩余到账股数(股)"],
            "转让收入(元)": r["转让收入(元)"],
            "转让费用(元)": r["转让费用(元)"],
            "转让税款(元)": r["转让税款(元)"],
            "转让净收益(元)": r["转让净收益(元)"],
            "单条记录净收益(元)": r["单条记录净收益(元)"],
            "最终应缴税额": round(r["行权/归属税款(元)"] + r["转让税款(元)"], 2)
        }
        # 税率适配
        if tax_resident == "中国大陆":
            form_data["应纳税所得额"] = yearly_result["年度行权/归属总收入(元)"]
            form_data["行权/归属适用税率"] = "3%-45%（单独计税）" if yearly_result["是否上市公司"] == "是" else "3%-45%（并入综合所得）"
            form_data["转让适用税率"] = "20%（财产转让所得）" if not rule["transfer_tax_exempt"][yearly_result["上市地"]] else "暂免"
        else:
            form_data["应纳税所得额"] = r["行权/归属收入(元)"]
            form_data["行权/归属适用税率"] = f"{rule['annual_brackets'][-1][1] * 100}%"
            form_data["转让适用税率"] = "0%（无资本利得税）"
        form_data_list.append(form_data)
    
    # 年度汇总行
    summary_form_data = {
        "记录ID": "年度汇总",
        "激励工具类型": "合并计算",
        "行权/归属方式": "——",
        "转让类型": "——",
        "行权/归属收入(元)": yearly_result["年度行权/归属总收入(元)"],
        "行权/归属税款(元)": yearly_result["年度行权/归属总税款(元)"],
        "抵税股出售数量(股)": yearly_result["年度总抵税股出售数量(股)"],
        "剩余到账股数(股)": "——",
        "转让收入(元)": yearly_result["年度转让总收入(元)"],
        "转让费用(元)": yearly_result["年度转让总费用(元)"],
        "转让税款(元)": yearly_result["年度转让总税款(元)"],
        "转让净收益(元)": yearly_result["年度转让净收益(元)"],
        "单条记录净收益(元)": yearly_result["年度单条净收益合计(元)"],
        "应纳税所得额": yearly_result["年度行权/归属总收入(元)"],
        "行权/归属适用税率": form_data["行权/归属适用税率"],
        "转让适用税率": form_data["转让适用税率"],
        "最终应缴税额": yearly_result["年度总税款(元)"]
    }
    form_data_list.append(summary_form_data)
    return pd.DataFrame(form_data_list)

# ---------------------- 结果导出函数 ----------------------
def export_to_excel(detail_results, yearly_result, tax_form_df):
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine="xlsxwriter")
    pd.DataFrame(detail_results).to_excel(writer, sheet_name="交易明细", index=False)
    pd.DataFrame([yearly_result]).to_excel(writer, sheet_name="年度汇总", index=False)
    tax_form_df.to_excel(writer, sheet_name="报税表单", index=False)
    writer.close()
    output.seek(0)
    return output

# ---------------------- 页面主体 ----------------------
st.title("股权激励计税工具（税款计算修正版）")
st.caption(TAX_RULES["中国大陆"]["policy_basis"])
st.divider()

# 全局参数初始化
if "tax_resident" not in st.session_state:
    st.session_state.tax_resident = "中国大陆"
if "is_listed" not in st.session_state:
    st.session_state.is_listed = True
if "listing_location" not in st.session_state:
    st.session_state.listing_location = "境内"
if "tax_threshold" not in st.session_state:
    st.session_state.tax_threshold = 10000.0
if "equity_records" not in st.session_state:
    # 测试用例：21.6万收入的场景
    st.session_state.equity_records = [
        {
            "id": 1,
            "incentive_tool": "期权",
            "exercise_method": "卖股/净股缴税",
            "transfer_type": "无转让",
            "exercise_price": 120.0,
            "exercise_quantity": 1800,
            "exercise_market_price": 240.0,  # 收入=(240-120)*1800=216000元
            "transfer_price": 0.0,
            "transfer_fee_rate": TRANSFER_TYPES["无转让"]["fee_rate"]
        }
    ]
else:
    # 兼容旧记录
    for record in st.session_state.equity_records:
        if "transfer_type" not in record:
            record["transfer_type"] = "无转让"
        if "transfer_fee_rate" not in record:
            record["transfer_fee_rate"] = TRANSFER_TYPES["无转让"]["fee_rate"]
        if "transfer_price" not in record:
            record["transfer_price"] = 0.0

# ---------------------- 侧边栏参数设置 ----------------------
with st.sidebar:
    st.header("参数设置")
    
    # 基础税务参数
    st.session_state.tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()))
    st.session_state.is_listed = st.checkbox("是否上市公司", value=True)
    st.session_state.listing_location = st.selectbox(
        "上市地", 
        ["境内", "境外"],
        help="中国大陆居民转让境内上市股票暂免财产转让所得税"
    )

    st.divider()
    # 税款标注阈值
    st.subheader("税款标注阈值")
    st.session_state.tax_threshold = st.slider(
        label="拖拽调整阈值",
        min_value=0.0,
        max_value=100000.0,
        step=1000.0,
        value=st.session_state.tax_threshold,
        format="%.0f 元"
    )
    st.markdown(f"""
    <div style="text-align: center; font-size: 16px; font-weight: 500; margin-top: -10px;">
        当前阈值：<span style="color: #333;">{st.session_state.tax_threshold:,.0f}</span> 元
    </div>
    """, unsafe_allow_html=True)

    # 综合所得扣除项
    other_income = 0.0
    special_deduction = 0.0
    if st.session_state.tax_resident == "中国大陆" and not st.session_state.is_listed:
        st.divider()
        st.subheader("综合所得扣除项")
        other_income = st.number_input("年度其他综合所得", min_value=0.0, step=1000.0, value=0.0)
        special_deduction = st.number_input("年度专项附加扣除", min_value=0.0, step=1000.0, value=0.0)

    st.divider()
    # 记录操作
    st.subheader("记录操作")
    col_add, col_del = st.columns(2)
    with col_add:
        if st.button("添加记录"):
            new_id = len(st.session_state.equity_records) + 1
            st.session_state.equity_records.append({
                "id": new_id,
                "incentive_tool": "期权",
                "exercise_method": "卖股/净股缴税",
                "transfer_type": "无转让",
                "exercise_price": 120.0,
                "exercise_quantity": 1800,
                "exercise_market_price": 240.0,
                "transfer_price": 0.0,
                "transfer_fee_rate": TRANSFER_TYPES["无转让"]["fee_rate"]
            })
    with col_del:
        if st.button("删除最后一条"):
            if len(st.session_state.equity_records) > 1:
                st.session_state.equity_records.pop()

    if st.button("重置参数"):
        st.session_state.clear()
        st.rerun()

    calc_btn = st.button("计算", use_container_width=True)

# ---------------------- 交易记录输入区 ----------------------
st.subheader("交易记录")
for idx, record in enumerate(st.session_state.equity_records):
    with st.expander(f"记录 {record['id']}", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            # 激励工具类型
            tool_keys = list(INCENTIVE_TOOLS.keys())
            try:
                tool_index = tool_keys.index(record["incentive_tool"])
            except (ValueError, KeyError):
                tool_index = 0
            record["incentive_tool"] = st.selectbox(
                "激励工具类型", tool_keys,
                index=tool_index,
                key=f"tool_{record['id']}",
                help=INCENTIVE_TOOLS[tool_keys[tool_index]]["income_formula"]
            )

            # 行权/归属方式
            method_keys = list(EXERCISE_METHODS.keys())
            if INCENTIVE_TOOLS[record["incentive_tool"]]["type"] == "现金结算类":
                method_keys = ["现金结算"]
            try:
                method_index = method_keys.index(record["exercise_method"])
            except (ValueError, KeyError):
                method_index = 0
            record["exercise_method"] = st.selectbox(
                "行权/归属方式", method_keys,
                index=method_index,
                key=f"method_{record['id']}",
                help=EXERCISE_METHODS[method_keys[method_index]]["desc"]
            )

            # 转让类型
            transfer_keys = list(TRANSFER_TYPES.keys())
            current_transfer = record.get("transfer_type", "无转让")
            try:
                transfer_index = transfer_keys.index(current_transfer)
            except ValueError:
                transfer_index = 0
            record["transfer_type"] = st.selectbox(
                "转让类型", transfer_keys,
                index=transfer_index,
                key=f"transfer_{record['id']}",
                help=TRANSFER_TYPES[transfer_keys[transfer_index]]["desc"]
            )

        with col2:
            # 价格/数量参数
            price_label = "行权价/授予价(元/股)"
            record["exercise_price"] = st.number_input(
                price_label, 
                min_value=0.0, 
                step=1.0, 
                value=record.get("exercise_price", 0.0), 
                key=f"price_{record['id']}",
                help="RSU填0（无授予价）"
            )
            record["exercise_quantity"] = st.number_input(
                "行权/归属数量(股)", 
                min_value=1, 
                step=100, 
                value=record.get("exercise_quantity", 100), 
                key=f"qty_{record['id']}"
            )
            record["exercise_market_price"] = st.number_input(
                "行权/归属日市价(元/股)", 
                min_value=0.0, 
                step=1.0, 
                value=record.get("exercise_market_price", 0.0), 
                key=f"mp_{record['id']}"
            )

        # 转让相关参数
        if record["transfer_type"] != "无转让":
            st.divider()
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                record["transfer_price"] = st.number_input(
                    "转让价(元/股)", 
                    min_value=0.0, 
                    step=1.0, 
                    value=record.get("transfer_price", 0.0), 
                    key=f"tp_{record['id']}"
                )
            with col_t2:
                default_fee = TRANSFER_TYPES[record["transfer_type"]]["fee_rate"]
                current_fee = record.get("transfer_fee_rate", default_fee)
                record["transfer_fee_rate"] = st.number_input(
                    "转让费用率(%)", 
                    min_value=0.0, 
                    max_value=1.0, 
                    step=0.05, 
                    value=round(current_fee * 100, 2), 
                    key=f"fee_{record['id']}"
                ) / 100
        else:
            record["transfer_price"] = 0.0
            record["transfer_fee_rate"] = 0.0
st.divider()

# ---------------------- 计算结果展示 ----------------------
if calc_btn:
    input_records = [r for r in st.session_state.equity_records if r.get("exercise_quantity", 0) > 0]
    if not input_records:
        st.error("无有效交易记录，请填写行权/归属数量")
    else:
        detail_results = [calculate_single_record(
            r, st.session_state.tax_resident, st.session_state.is_listed, st.session_state.listing_location
        ) for r in input_records]
        yearly_result = calculate_yearly_consolidation(
            detail_results, st.session_state.tax_resident, st.session_state.is_listed,
            st.session_state.listing_location, other_income, special_deduction
        )
        tax_form_df = generate_tax_form(yearly_result, detail_results, st.session_state.tax_resident)

        # 1. 关键指标仪表盘
        st.subheader("关键指标仪表盘")
        col_sold = st.columns(1)[0]
        with col_sold:
            st.metric(
                label="年度总抵税股出售数量",
                value=f"{yearly_result['年度总抵税股出售数量(股)']} 股"
            )
        st.markdown("---")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(label="年度行权/归属总收入", value=f"¥ {yearly_result['年度行权/归属总收入(元)']:,.2f}")
        with col2:
            st.metric(label="年度转让净收益", value=f"¥ {yearly_result['年度转让净收益(元)']:,.2f}")
        with col3:
            st.metric(label="年度总税款", value=f"¥ {yearly_result['年度总税款(元)']:,.2f}")
        with col4:
            st.metric(label="年度净收益", value=f"¥ {yearly_result['年度净收益(元)']:,.2f}")
        st.divider()

        # 2. 交易明细
        st.subheader("交易明细")
        show_cols = [
            "记录ID", "激励工具类型", "行权/归属方式", "转让类型",
            "行权/授予价(元/股)", "行权/归属数量(股)", "行权/归属日市价(元/股)",
            "行权/归属收入(元)", "行权/归属税款(元)",
            "抵税股出售数量(股)", "剩余到账股数(股)", "实际持有数量(股)",
            "转让价(元/股)", "转让费用(元)", "转让收入(元)", "转让税款(元)",
            "转让净收益(元)", "单条记录净收益(元)"
        ]
        detail_df = pd.DataFrame(detail_results)[show_cols]
        column_config = {
            "记录ID": st.column_config.TextColumn("记录ID", width="small"),
            "激励工具类型": st.column_config.TextColumn("工具类型", width="medium"),
            "行权/归属方式": st.column_config.TextColumn("行权/归属方式", width="medium"),
            "转让类型": st.column_config.TextColumn("转让类型", width="medium"),
            "行权/授予价(元/股)": st.column_config.NumberColumn("行权/授予价", width="small", format="%.2f"),
            "行权/归属数量(股)": st.column_config.NumberColumn("总数量", width="small", format="%d"),
            "行权/归属日市价(元/股)": st.column_config.NumberColumn("市价", width="small", format="%.2f"),
            "行权/归属收入(元)": st.column_config.NumberColumn("行权/归属收入", width="medium", format="%,.2f"),
            "行权/归属税款(元)": st.column_config.NumberColumn("行权/归属税款", width="medium", format="%,.2f"),
            "抵税股出售数量(股)": st.column_config.TextColumn("抵税股数", width="small"),
            "剩余到账股数(股)": st.column_config.TextColumn("剩余股数", width="small"),
            "实际持有数量(股)": st.column_config.NumberColumn("实际持股", width="small", format="%d"),
            "转让价(元/股)": st.column_config.NumberColumn("转让价", width="small", format="%.2f"),
            "转让费用(元)": st.column_config.NumberColumn("转让费用", width="small", format="%,.2f"),
            "转让收入(元)": st.column_config.NumberColumn("转让收入", width="medium", format="%,.2f"),
            "转让税款(元)": st.column_config.NumberColumn("转让税款", width="medium", format="%,.2f"),
            "转让净收益(元)": st.column_config.NumberColumn("转让净收益", width="medium", format="%,.2f"),
            "单条记录净收益(元)": st.column_config.NumberColumn("单条净收益", width="medium", format="%,.2f")
        }
        styled_detail = apply_tax_highlight(
            detail_df, ["行权/归属税款(元)", "转让税款(元)"], st.session_state.tax_threshold
        )
        st.dataframe(styled_detail, column_config=column_config, use_container_width=True)
        st.divider()

        # 3. 年度汇总
        st.subheader("年度汇总")
        summary_cols = [
            "税务居民身份", "是否上市公司", "上市地",
            "年度行权/归属总收入(元)", "年度行权/归属总税款(元)",
            "年度总抵税股出售数量(股)",
            "年度转让总收入(元)", "年度转让总费用(元)", "年度转让总税款(元)",
            "年度转让净收益(元)", "年度单条净收益合计(元)",
            "年度总税款(元)", "年度净收益(元)", "计税规则说明"
        ]
        summary_df = pd.DataFrame([yearly_result])[summary_cols]
        summary_config = {
            "税务居民身份": st.column_config.TextColumn("税务身份", width="small"),
            "是否上市公司": st.column_config.TextColumn("是否上市", width="small"),
            "上市地": st.column_config.TextColumn("上市地", width="small"),
            "年度行权/归属总收入(元)": st.column_config.NumberColumn("行权/归属收入", width="medium", format="%,.2f"),
            "年度行权/归属总税款(元)": st.column_config.NumberColumn("行权/归属税款", width="medium", format="%,.2f"),
            "年度总抵税股出售数量(股)": st.column_config.NumberColumn("总抵税股数", width="small", format="%d"),
            "年度转让总收入(元)": st.column_config.NumberColumn("转让收入", width="medium", format="%,.2f"),
            "年度转让总费用(元)": st.column_config.NumberColumn("转让费用", width="medium", format="%,.2f"),
            "年度转让总税款(元)": st.column_config.NumberColumn("转让税款", width="medium", format="%,.2f"),
            "年度转让净收益(元)": st.column_config.NumberColumn("转让净收益", width="medium", format="%,.2f"),
            "年度单条净收益合计(元)": st.column_config.NumberColumn("单条净收益合计", width="medium", format="%,.2f"),
            "年度总税款(元)": st.column_config.NumberColumn("总税款", width="medium", format="%,.2f"),
            "年度净收益(元)": st.column_config.NumberColumn("年度净收益", width="medium", format="%,.2f"),
            "计税规则说明": st.column_config.TextColumn("计税规则", width="large")
        }
        styled_summary = apply_tax_highlight(
            summary_df, ["年度行权/归属总税款(元)", "年度转让总税款(元)", "年度总税款(元)"], st.session_state.tax_threshold
        )
        st.dataframe(styled_summary, column_config=summary_config, use_container_width=True)
        st.divider()

        # 4. 税款构成饼图
        st.subheader("税款构成")
        tax_data = pd.DataFrame({
            "税款类型": ["行权/归属税款", "转让税款"],
            "金额(元)": [yearly_result["年度行权/归属总税款(元)"], yearly_result["年度转让总税款(元)"]]
        })
        fig = px.pie(
            tax_data, values="金额(元)", names="税款类型", hole=0.4,
            color_discrete_sequence=["#dcdcdc", "#c0c0c0"]
        )
        fig.update_layout(
            showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
            font=dict(size=12, color="#333333")
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)
        st.divider()

        # 5. 报税表单
        st.subheader("报税表单")
        form_config = {
            "记录ID": st.column_config.TextColumn("记录ID", width="small"),
            "激励工具类型": st.column_config.TextColumn("工具类型", width="medium"),
            "行权/归属方式": st.column_config.TextColumn("行权/归属方式", width="medium"),
            "转让类型": st.column_config.TextColumn("转让类型", width="medium"),
            "行权/归属收入(元)": st.column_config.NumberColumn("行权/归属收入", width="medium", format="%,.2f"),
            "行权/归属税款(元)": st.column_config.NumberColumn("行权/归属税款", width="medium", format="%,.2f"),
            "抵税股出售数量(股)": st.column_config.TextColumn("抵税股数", width="small"),
            "剩余到账股数(股)": st.column_config.TextColumn("剩余股数", width="small"),
            "转让收入(元)": st.column_config.NumberColumn("转让收入", width="medium", format="%,.2f"),
            "转让费用(元)": st.column_config.NumberColumn("转让费用", width="medium", format="%,.2f"),
            "转让税款(元)": st.column_config.NumberColumn("转让税款", width="medium", format="%,.2f"),
            "转让净收益(元)": st.column_config.NumberColumn("转让净收益", width="medium", format="%,.2f"),
            "单条记录净收益(元)": st.column_config.NumberColumn("单条净收益", width="medium", format="%,.2f"),
            "应纳税所得额": st.column_config.NumberColumn("应纳税所得额", width="medium", format="%,.2f"),
            "行权/归属适用税率": st.column_config.TextColumn("行权/归属税率", width="small"),
            "转让适用税率": st.column_config.TextColumn("转让税率", width="small"),
            "最终应缴税额": st.column_config.NumberColumn("最终税额", width="medium", format="%,.2f")
        }
        styled_form = apply_tax_highlight(
            tax_form_df, ["行权/归属税款(元)", "转让税款(元)", "最终应缴税额"], st.session_state.tax_threshold
        )
        st.dataframe(styled_form, column_config=form_config, use_container_width=True)
        st.divider()

        # 6. Excel导出
        st.subheader("结果导出")
        excel_data = export_to_excel(detail_results, yearly_result, tax_form_df)
        st.download_button(
            label="导出Excel文件（修正版）",
            data=excel_data,
            file_name="股权激励计税结果_税款修正版.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# 免责声明
st.divider()
st.caption("本工具已修正超额累进税率计算逻辑，结果符合个税规则 | 实际计税请以税务机关核定为准")
