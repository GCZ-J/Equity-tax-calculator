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
from datetime import datetime
import io

# ---------------------- 页面基础配置 ----------------------
st.set_page_config(
    page_title="股权激励个税计算器（精准计税版）",
    page_icon="🧮",
    layout="wide"
)

# ---------------------- 核心规则配置 ----------------------
# 1. 激励工具规则
INCENTIVE_TOOLS = {
    "期权（Option）": {
        "income_formula": "行权收入 =（行权日市价 - 行权价）× 实际行权数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    },
    "限制性股票（RSU）": {
        "income_formula": "行权/解禁收入 = 解禁日市价 × 解禁数量（无行权价）",
        "income_calc": lambda ep, mp, q, *args: mp * q
    },
    "股票增值权（SAR）": {
        "income_formula": "行权收入 =（行权日市价 - 授予价）× 行权数量（现金结算）",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    }
}

# 2. 行权方式规则（每条记录独立选择）
EXERCISE_METHODS = {
    "现金行权（Cash Exercise）": {
        "desc": "以现金支付行权价，全额持有股票",
        "actual_quantity": lambda q, tax, ep, mp: q,
        "formula": "实际持有数量=行权数量"
    },
    "卖股缴税（Sell to Cover）": {
        "desc": "卖出部分股票支付【单条预计算税款】，剩余股票持有",
        "actual_quantity": lambda q, tax, ep, mp: q - (tax / (mp or 1)),
        "formula": "实际持有数量=行权数量 - （单条预计算税款÷行权日市价）"
    },
    "无现金行权（Cashless Hold）": {
        "desc": "券商垫付行权价，卖出部分股票偿还，剩余持有",
        "actual_quantity": lambda q, tax, ep, mp: q - ((ep*q + tax) / (mp or 1)),
        "formula": "实际持有数量=行权数量 - （行权总价+单条预计算税款）÷行权日市价"
    }
}

# 3. 多地区税务规则（中国大陆区分A/B表）
TAX_RULES = {
    "中国大陆": {
        "exercise_tax_type": "综合所得",
        "exercise_tax_brackets": [
            (36000, 0.03, 0), (144000, 0.1, 2520), (300000, 0.2, 16920),
            (420000, 0.25, 31920), (660000, 0.3, 52920), (960000, 0.35, 85920),
            (float('inf'), 0.45, 181920)
        ],
        "transfer_tax_rate": 0.2,
        "transfer_tax_exempt": True,  # 境内上市转让免税
        "exercise_tax_formula": "行权税款=（年度全部行权收入+其他综合所得-60000-专项附加扣除）×对应税率-速算扣除数",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×20%（境外上市）；境内上市转让免税",
        "tax_form_A": "个人所得税综合所得年度汇算申报表（A表）",
        "tax_form_B": "个人所得税综合所得年度汇算申报表（B表）",
        "form_fields": ["纳税人识别号", "任职受雇单位", "股权激励类型", "行权方式", "行权/解禁日期", "行权收入", "应纳税所得额", "适用税率", "速算扣除数", "应缴税额", "已预缴税额", "补/退税额"]
    },
    "中国香港": {
        "exercise_tax_type": "薪俸税",
        "exercise_tax_brackets": [
            (50000, 0.02, 0), (50000, 0.06, 1000), (50000, 0.1, 3000),
            (50000, 0.14, 5000), (float('inf'), 0.17, 7000)
        ],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数（薪俸税，免税额简化为0）",
        "transfer_tax_formula": "转让税款=0（香港无资本利得税）",
        "tax_form": "个别人士报税表（BIR60）",
        "form_fields": ["香港身份证号", "雇主名称", "入息年度", "股权激励入息金额", "应评税入息", "适用税率", "应缴薪俸税额", "已缴暂缴薪俸税", "应补/退税额"]
    },
    "新加坡": {
        "exercise_tax_type": "个人所得税",
        "exercise_tax_brackets": [
            (20000, 0.02, 0), (10000, 0.035, 400), (10000, 0.07, 750),
            (40000, 0.115, 1150), (40000, 0.15, 2750), (40000, 0.18, 4750),
            (40000, 0.19, 6550), (40000, 0.2, 8150), (float('inf'), 0.22, 8950)
        ],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数",
        "transfer_tax_formula": "转让税款=0（新加坡无资本利得税）",
        "tax_form": "个人所得税申报表（Form B1/B）",
        "form_fields": ["NRIC/FIN号", "雇主编号", "评税年度", "就业收入（含股权激励）", "应纳税所得额", "适用税率", "应缴税额", "预扣税", "补/退税额"]
    },
    "阿联酋": {
        "exercise_tax_type": "无个税",
        "exercise_tax_brackets": [(float('inf'), 0.0, 0)],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=0（阿联酋无个人所得税）",
        "transfer_tax_formula": "转让税款=0（阿联酋无资本利得税）",
        "tax_form": "无个税申报要求（附收入证明）",
        "form_fields": ["护照号", "雇主名称", "收入期间", "股权激励收入金额", "转让收益金额", "无应缴税额说明"]
    },
    "德国": {
        "exercise_tax_type": "所得税",
        "exercise_tax_brackets": [
            (9984, 0.0, 0), (8632, 0.14, 0), (107394, 0.42, 950.96),
            (float('inf'), 0.45, 3666.84)
        ],
        "transfer_tax_rate": 0.25,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数（所得税14%-45%）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×25%（含团结税）",
        "tax_form": "所得税申报表（Meldeformular 100）",
        "form_fields": ["税号（Steuernummer）", "雇主名称", "报税年度", "工作收入（股权激励）", "资本利得（转让）", "应纳税所得额", "所得税率", "资本利得税率", "总应缴税额", "预扣税"]
    },
    "法国": {
        "exercise_tax_type": "所得税",
        "exercise_tax_brackets": [
            (11294, 0.0, 0), (28797, 0.11, 0), (28797, 0.3, 3167.67),
            (75550, 0.41, 11706.78), (float('inf'), 0.45, 14728.78)
        ],
        "transfer_tax_rate": 0.30,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数（所得税0%-45%）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×30%（含社会捐税）",
        "tax_form": "所得税申报表（Form 2042C）",
        "form_fields": ["税号（Numéro de fiscal）", "雇主名称", "报税年度", "就业收入（股权激励）", "资本利得", "应纳税所得额", "适用税率", "社会捐税率", "总应缴税额", "预扣税款"]
    },
    "美国（加州）": {
        "exercise_tax_type": "联邦+州税",
        "exercise_tax_brackets": [
            (11600, 0.10, 0), (47150, 0.12, 1160), (100525, 0.22, 5928),
            (191950, 0.24, 17602), (243725, 0.32, 34648), (609350, 0.35, 47836),
            (float('inf'), 0.37, 65469)
        ],
        "state_tax_rate": 0.123,
        "transfer_tax_rate": 0.20,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=（行权收入×联邦税率-速算扣除数）+（行权收入×加州州税12.3%）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×（联邦20%+加州12.3%）",
        "tax_form": "联邦1040表+加州540表",
        "form_fields": ["社安号（SSN）", "雇主EIN号", "报税年度", "工薪收入（股权激励）", "资本利得（转让）", "联邦应纳税所得额", "联邦税率", "加州州税应纳税所得额", "州税率", "总应缴税额", "预扣税"]
    },
    "美国（德州）": {
        "exercise_tax_type": "联邦税（无州税）",
        "exercise_tax_brackets": [
            (11600, 0.10, 0), (47150, 0.12, 1160), (100525, 0.22, 5928),
            (191950, 0.24, 17602), (243725, 0.32, 34648), (609350, 0.35, 47836),
            (float('inf'), 0.37, 65469)
        ],
        "state_tax_rate": 0.0,
        "transfer_tax_rate": 0.20,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=行权收入×联邦税率-速算扣除数（无州税）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×联邦20%（无州税）",
        "tax_form": "联邦1040表（无州税申报表）",
        "form_fields": ["社安号（SSN）", "雇主EIN号", "报税年度", "工薪收入（股权激励）", "资本利得（转让）", "联邦应纳税所得额", "联邦税率", "应缴联邦税额", "预扣税", "补/退税额"]
    }
}

# ---------------------- 税率计算工具函数 ----------------------
def calculate_tax_brackets(income, brackets):
    """按超额累进税率计算税款"""
    tax = 0.0
    remaining = max(income, 0.0)
    for bracket, rate, deduction in brackets:
        if remaining <= 0:
            break
        taxable = min(remaining, bracket)
        tax += taxable * rate - deduction
        remaining -= taxable
    return round(tax, 2)

# ---------------------- 核心计算函数 ----------------------
def calculate_single_record(record, tax_resident, listing_location):
    """计算单条股权激励记录的收入和基础数据（不合并计税）"""
    # 提取单条记录参数
    record_id = record["id"]
    incentive_tool = record["incentive_tool"]
    exercise_method = record["exercise_method"]
    ep = record["exercise_price"]
    eq = record["exercise_quantity"]
    mp = record["exercise_market_price"]
    tp = record["transfer_price"]

    # 1. 计算单条行权收入
    exercise_income = INCENTIVE_TOOLS[incentive_tool]["income_calc"](ep, mp, eq)
    exercise_income = max(exercise_income, 0.0)

    # 2. 计算单条预计算行权税款（用于sell to cover计算持股数，非最终合并税款）
    rule = TAX_RULES[tax_resident]
    pre_exercise_tax = calculate_tax_brackets(exercise_income, rule["exercise_tax_brackets"])
    if tax_resident == "美国（加州）":
        pre_exercise_tax += exercise_income * rule["state_tax_rate"]
    pre_exercise_tax = round(pre_exercise_tax, 2)

    # 3. 计算单条实际持有数量（根据行权方式，核心用预计算税款）
    actual_qty = EXERCISE_METHODS[exercise_method]["actual_quantity"](eq, pre_exercise_tax, ep, mp)
    actual_qty = max(round(actual_qty, 2), 0.0)

    # 4. 计算单条转让收入和转让税款（单独计税，不合并）
    transfer_income = 0.0
    transfer_tax = 0.0
    if tp > 0 and actual_qty > 0:
        transfer_income = (tp - mp) * actual_qty
        transfer_income = max(transfer_income, 0.0)
        # 转让税款：境外上市计税，境内上市免税（中国大陆）
        if not (rule["transfer_tax_exempt"] and listing_location == "境内"):
            transfer_tax = transfer_income * rule["transfer_tax_rate"]
            if tax_resident == "美国（加州）":
                transfer_tax += transfer_income * rule["state_tax_rate"]
        transfer_tax = round(transfer_tax, 2)

    # 整理单条记录数据（统一列名，避免后续KeyError）
    return {
        "记录ID": record_id,
        "激励工具类型": incentive_tool,
        "行权方式": exercise_method,
        "行权价/授予价(元/股)": ep,
        "行权/解禁数量(股)": eq,
        "行权/解禁日市价(元/股)": mp,
        "转让价(元/股)": tp,
        "行权收入(元)": exercise_income,
        "预计算行权税款(元)": pre_exercise_tax,  # sell to cover的计算依据
        "实际持有数量(股)": actual_qty,
        "转让收入(元)": transfer_income,
        "转让税款(元)": transfer_tax,
        "行权方式计算公式": EXERCISE_METHODS[exercise_method]["formula"]
    }

def calculate_yearly_consolidation(detail_records, tax_resident, listing_location, other_income, special_deduction):
    """年度合并计税：综合所得（行权）+ 财产转让所得（转让）"""
    rule = TAX_RULES[tax_resident]
    
    # 1. 汇总行权相关数据
    total_exercise_income = sum([r["行权收入(元)"] for r in detail_records])
    # 汇总转让相关数据
    total_transfer_income = sum([r["转让收入(元)"] for r in detail_records])
    total_transfer_tax = sum([r["转让税款(元)"] for r in detail_records])

    # 2. 合并计算综合所得税款（行权收入）
    total_exercise_tax = 0.0
    taxable_income = 0.0  # 新增：记录应纳税所得额，方便排查
    if rule["exercise_tax_type"] != "无个税":
        if tax_resident == "中国大陆":
            # 综合所得应纳税所得额 = 行权收入 + 其他综合所得 - 6万 - 专项附加扣除
            taxable_income = max(total_exercise_income + other_income - 60000 - special_deduction, 0.0)
            total_exercise_tax = calculate_tax_brackets(taxable_income, rule["exercise_tax_brackets"])
        else:
            # 其他地区直接按行权收入计税
            taxable_income = max(total_exercise_income, 0.0)
            total_exercise_tax = calculate_tax_brackets(taxable_income, rule["exercise_tax_brackets"])
            if tax_resident == "美国（加州）":
                total_exercise_tax += total_exercise_income * rule["state_tax_rate"]
    total_exercise_tax = round(total_exercise_tax, 2)

    # 3. 计算年度总税款
    total_yearly_tax = round(total_exercise_tax + total_transfer_tax, 2)
    total_yearly_income = round(total_exercise_income + total_transfer_income, 2)
    net_income = round(total_yearly_income - total_yearly_tax, 2)

    # 4. 确定适用报税表单（中国大陆A/B表）
    if tax_resident == "中国大陆":
        tax_form = rule["tax_form_A"] if listing_location == "境内" else rule["tax_form_B"]
    else:
        tax_form = rule["tax_form"]

    # 整理年度合并结果（新增应纳税所得额）
    return {
        "税务居民身份": tax_resident,
        "上市地": listing_location,
        "年度其他综合所得(元)": other_income,
        "年度专项附加扣除(元)": special_deduction,
        "年度汇总行权收入(元)": total_exercise_income,
        "年度应纳税所得额(元)": taxable_income,  # 新增：展示扣除后数值
        "年度综合所得税款(元)": total_exercise_tax,
        "年度汇总转让收入(元)": total_transfer_income,
        "年度财产转让税款(元)": total_transfer_tax,
        "年度总税款(元)": total_yearly_tax,
        "年度总收益(元)": total_yearly_income,
        "年度净收益(元)": net_income,
        "适用报税表单": tax_form,
        "计税说明": "1. 行权收入计入综合所得合并计税；2. 转让收入计入财产转让所得单独计税；3. sell to cover用单条预计算税款"
    }

# ---------------------- 报税表单生成函数 ----------------------
def generate_tax_form(yearly_result, detail_records, tax_resident):
    """生成包含明细的报税表单"""
    rule = TAX_RULES[tax_resident]
    form_data_list = []

    # 单条记录明细
    for r in detail_records:
        form_data = {
            "记录ID": r["记录ID"],
            "股权激励类型": r["激励工具类型"],
            "行权方式": r["行权方式"],
            "行权收入(元)": r["行权收入(元)"],
            "预计算行权税款(元)": r["预计算行权税款(元)"],
            "转让收入(元)": r["转让收入(元)"],
            "转让税款(元)": r["转让税款(元)"]
        }
        # 补充通用字段
        for field in rule["form_fields"]:
            if field not in form_data:
                if field == "应纳税所得额" and tax_resident == "中国大陆":
                    form_data[field] = yearly_result["年度应纳税所得额(元)"]
                elif field == "适用税率":
                    form_data[field] = "3%-45%（超额累进）" if tax_resident == "中国大陆" else f"{rule['exercise_tax_brackets'][-1][1] * 100}%"
                elif field == "应缴税额":
                    form_data[field] = yearly_result["年度总税款(元)"]
                else:
                    form_data[field] = "__________"
        form_data_list.append(form_data)
    
    # 汇总行
    summary_form_data = {
        "记录ID": "年度汇总",
        "股权激励类型": "多种工具合并",
        "行权方式": "——",
        "行权收入(元)": yearly_result["年度汇总行权收入(元)"],
        "预计算行权税款(元)": "——",
        "转让收入(元)": yearly_result["年度汇总转让收入(元)"],
        "转让税款(元)": yearly_result["年度财产转让税款(元)"]
    }
    for field in rule["form_fields"]:
        if field not in summary_form_data:
            if field == "应纳税所得额" and tax_resident == "中国大陆":
                summary_form_data[field] = yearly_result["年度应纳税所得额(元)"]
            elif field == "适用税率":
                summary_form_data[field] = "3%-45%（超额累进）" if tax_resident == "中国大陆" else f"{rule['exercise_tax_brackets'][-1][1] * 100}%"
            elif field == "应缴税额":
                summary_form_data[field] = yearly_result["年度总税款(元)"]
            else:
                summary_form_data[field] = "__________"
    form_data_list.append(summary_form_data)

    return pd.DataFrame(form_data_list)

# ---------------------- 结果导出函数 ----------------------
def export_to_excel(detail_records, yearly_result, tax_form_df):
    """导出单条明细+年度汇总+报税表单"""
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine="xlsxwriter")
    pd.DataFrame(detail_records).to_excel(writer, sheet_name="单条交易明细", index=False)
    pd.DataFrame([yearly_result]).to_excel(writer, sheet_name="年度合并计税结果", index=False)
    tax_form_df.to_excel(writer, sheet_name="报税表单模板", index=False)
    writer.close()
    output.seek(0)
    return output

# ---------------------- Streamlit 界面 ----------------------
st.title("🧮 股权激励个税计算器（精准计税版）")
st.markdown("### 单条记录独立行权方式 | 年度合并计税 | 综合所得+财产转让所得区分")
st.divider()

# ---------------------- 1. 全局参数初始化 ----------------------
# 全局参数（所有记录共用）
if "tax_resident" not in st.session_state:
    st.session_state.tax_resident = "中国大陆"
if "listing_location" not in st.session_state:
    st.session_state.listing_location = "境外"
if "other_income" not in st.session_state:
    st.session_state.other_income = 0.0
if "special_deduction" not in st.session_state:
    st.session_state.special_deduction = 0.0

# 多记录存储（每条记录含独立行权方式）
if "equity_records" not in st.session_state:
    st.session_state.equity_records = [
        {
            "id": 1,
            "incentive_tool": "期权（Option）",
            "exercise_method": "卖股缴税（Sell to Cover）",  # 默认改为sell to cover方便测试
            "exercise_price": 10.0,
            "exercise_quantity": 1000,
            "exercise_market_price": 50.0,  # 提高市价，让行权收入和预缴税不为0
            "transfer_price": 0.0
        }
    ]

# ---------------------- 2. 侧边栏：全局参数设置 ----------------------
with st.sidebar:
    st.header("🌐 全局参数（所有记录共用）")
    st.session_state.tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()), index=list(TAX_RULES.keys()).index(st.session_state.tax_resident))
    st.session_state.listing_location = st.selectbox("上市地", ["境内", "境外"], index=["境内", "境外"].index(st.session_state.listing_location))
    
    st.subheader("💰 年度扣除项（仅中国大陆适用）")
    st.session_state.other_income = st.number_input("年度其他综合所得(元)", min_value=0.0, step=1000.0, value=st.session_state.other_income)
    st.session_state.special_deduction = st.number_input("年度专项附加扣除(元)", min_value=0.0, step=1000.0, value=st.session_state.special_deduction)

    st.divider()
    st.header("📝 记录操作")
    col_add, col_del = st.columns(2)
    with col_add:
        if st.button("➕ 添加交易记录", type="primary"):
            new_id = len(st.session_state.equity_records) + 1
            st.session_state.equity_records.append({
                "id": new_id,
                "incentive_tool": "期权（Option）",
                "exercise_method": "卖股缴税（Sell to Cover）",
                "exercise_price": 10.0,
                "exercise_quantity": 1000,
                "exercise_market_price": 50.0,
                "transfer_price": 0.0
            })
    with col_del:
        if st.button("➖ 删除最后一条", disabled=len(st.session_state.equity_records) <= 1):
            st.session_state.equity_records.pop()
    
    if st.button("🔄 重置所有参数"):
        st.session_state.clear()
        st.rerun()

    # 计算按钮
    calc_btn = st.button("📊 计算年度税款", type="secondary", use_container_width=True)

# ---------------------- 3. 主界面：单条交易记录输入 ----------------------
st.subheader("📋 股权激励交易记录（每条独立设置行权方式）")
st.markdown("#### 每条记录可选择不同的激励工具和行权方式")

for idx, record in enumerate(st.session_state.equity_records):
    with st.expander(f"交易记录 {record['id']}", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            record["incentive_tool"] = st.selectbox(
                "激励工具类型", list(INCENTIVE_TOOLS.keys()),
                index=list(INCENTIVE_TOOLS.keys()).index(record["incentive_tool"]),
                key=f"tool_{record['id']}"
            )
        with col2:
            record["exercise_method"] = st.selectbox(
                "行权方式", list(EXERCISE_METHODS.keys()),
                index=list(EXERCISE_METHODS.keys()).index(record["exercise_method"]),
                key=f"method_{record['id']}"
            )
        with col3:
            price_label = "行权价/授予价(元/股)" if record["incentive_tool"] != "限制性股票（RSU）" else "RSU填0"
            record["exercise_price"] = st.number_input(
                price_label, min_value=0.0, step=0.1, value=record["exercise_price"],
                key=f"price_{record['id']}"
            )
            record["exercise_quantity"] = st.number_input(
                "行权数量(股)", min_value=0, step=100, value=record["exercise_quantity"],
                key=f"qty_{record['id']}"
            )
        with col4:
            record["exercise_market_price"] = st.number_input(
                "行权日市价(元/股)", min_value=0.0, step=0.1, value=record["exercise_market_price"],
                key=f"mp_{record['id']}"
            )
            record["transfer_price"] = st.number_input(
                "转让价(元/股，未转让填0)", min_value=0.0, step=0.1, value=record["transfer_price"],
                key=f"tp_{record['id']}"
            )
    st.divider()

# ---------------------- 4. 计算与结果展示 ----------------------
if calc_btn:
    # 1. 校验记录有效性
    valid_records = []
    for r in st.session_state.equity_records:
        if r["exercise_quantity"] <= 0:
            st.warning(f"⚠️ 记录{r['id']}：行权数量不能为0！")
        elif r["exercise_market_price"] < r["exercise_price"] and r["incentive_tool"] != "限制性股票（RSU）":
            st.warning(f"⚠️ 记录{r['id']}：市价低于行权价，行权收入为0")
            valid_records.append(r)
        else:
            valid_records.append(r)
    
    if not valid_records:
        st.error("❌ 无有效交易记录，请检查输入！")
    else:
        # 2. 计算单条记录基础数据
        detail_results = [calculate_single_record(r, st.session_state.tax_resident, st.session_state.listing_location) for r in valid_records]
        # 3. 年度合并计税
        yearly_result = calculate_yearly_consolidation(
            detail_results,
            st.session_state.tax_resident,
            st.session_state.listing_location,
            st.session_state.other_income,
            st.session_state.special_deduction
        )
        # 4. 生成报税表单
        tax_form_df = generate_tax_form(yearly_result, detail_results, st.session_state.tax_resident)

        st.success("✅ 计算完成！先展示单条明细，再展示年度合并结果")

        # 4.1 单条交易明细（核心：展示预计算行权税款）
        st.subheader("📈 单条交易明细数据")
        show_detail_cols = [
            "记录ID", "激励工具类型", "行权方式", "行权价/授予价(元/股)", 
            "行权/解禁数量(股)", "行权/解禁日市价(元/股)", "行权收入(元)", 
            "预计算行权税款(元)", "实际持有数量(股)", "转让收入(元)", "转让税款(元)"
        ]
        detail_df = pd.DataFrame(detail_results)
        st.dataframe(detail_df[show_detail_cols], use_container_width=True)

        # 4.2 年度合并计税结果（新增应纳税所得额，方便排查）
        st.subheader("📊 年度合并计税结果")
        st.dataframe(pd.DataFrame([yearly_result]), use_container_width=True)

        # 4.3 关键说明：解释预缴税和合并税款的区别
        st.warning("⚠️ 关键说明：sell to cover计算持股数用的是【预计算行权税款】，不是合并后的综合所得税款！合并税款为0是因为扣除项抵消了收入。")

        # 4.4 税款构成可视化
        st.subheader("📉 年度税款构成分析")
        tax_data = pd.DataFrame({
            "税款类型": ["综合所得税款（行权）", "财产转让税款（转让）"],
            "金额（元）": [yearly_result["年度综合所得税款(元)"], yearly_result["年度财产转让税款(元)"]]
        })
        if yearly_result["年度总税款(元)"] > 0:
            fig = px.pie(
                tax_data, values="金额（元）", names="税款类型",
                title=f"年度总税款：{yearly_result['年度总税款(元)']:.2f} 元",
                hole=0.3, color_discrete_sequence=["#FF6B6B", "#4ECDC4"]
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("✅ 年度合并后无应缴税款，但单条预计算税款仍会影响sell to cover的持股数！")

        # 4.5 报税表单模板
        st.subheader("📋 年度报税表单模板（含明细+汇总）")
        st.dataframe(tax_form_df, use_container_width=True)

        # 4.6 导出功能
        st.subheader("📥 结果导出")
        col_excel, col_csv = st.columns(2)
        with col_excel:
            excel_data = export_to_excel(detail_results, yearly_result, tax_form_df)
            st.download_button(
                label="📊 导出Excel（明细+汇总+报税表）",
                data=excel_data,
                file_name=f"股权激励年度计税结果_{datetime.now().strftime('%Y%m%d%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        with col_csv:
            csv_data = detail_df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="📄 导出CSV（单条明细）",
                data=csv_data,
                file_name=f"股权激励交易明细_{datetime.now().strftime('%Y%m%d%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )

# ---------------------- 免责声明 ----------------------
st.divider()
st.markdown("""
> ⚠️ 免责声明：本工具为税务参考工具，实际税款及报税请以当地税务机关核定和官方表单为准，建议咨询专业税务师。
> 📌 功能说明：单条记录独立行权方式、年度合并计税、区分综合所得与财产转让所得、Excel/CSV导出。
""")
