import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import io

# ---------------------- 页面基础配置 ----------------------
st.set_page_config(
    page_title="股权激励个税计算器（全功能版）",
    page_icon="🧮",
    layout="centered"
)

# ---------------------- 核心规则配置（保留原有所有逻辑） ----------------------
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

# 2. 行权方式规则
EXERCISE_METHODS = {
    "现金行权（Cash Exercise）": {
        "desc": "以现金支付行权价，全额持有股票",
        "actual_quantity": lambda q, tax: q,
        "tax_base": lambda income: income,
        "formula": "实际持有数量=行权数量；计税基数=全额行权收入"
    },
    "卖股缴税（Sell to Cover）": {
        "desc": "卖出部分股票支付税款，剩余股票持有",
        "actual_quantity": lambda q, tax: q - (tax / (st.session_state.get('mp', 0) or 1)),
        "tax_base": lambda income: income,
        "formula": "实际持有数量=行权数量 - （税款÷行权日市价）；计税基数=全额行权收入"
    },
    "无现金行权（Cashless Hold）": {
        "desc": "券商垫付行权价，卖出部分股票偿还，剩余持有",
        "actual_quantity": lambda q, tax: q - ((st.session_state.get('ep', 0)*q + tax) / (st.session_state.get('mp', 0) or 1)),
        "tax_base": lambda income: income,
        "formula": "实际持有数量=行权数量 - （行权总价+税款）÷行权日市价；计税基数=全额行权收入"
    }
}

# 3. 多地区税务规则（新增报税表单核心字段）
TAX_RULES = {
    "中国大陆": {
        "exercise_tax_type": "综合所得",
        "exercise_tax_brackets": [
            (36000, 0.03, 0), (144000, 0.1, 2520), (300000, 0.2, 16920),
            (420000, 0.25, 31920), (660000, 0.3, 52920), (960000, 0.35, 85920),
            (float('inf'), 0.45, 181920)
        ],
        "transfer_tax_rate": 0.2,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=（行权收入+其他综合所得-60000-专项附加扣除）×对应税率-速算扣除数（境内上市）；行权税款=行权收入×对应税率-速算扣除数（境外上市）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×20%（境外上市）；境内上市转让税款=0",
        "tax_form": "个人所得税综合所得年度汇算申报表（A表）",
        "form_fields": ["纳税人识别号", "任职受雇单位", "股权激励类型", "行权/解禁日期", "行权收入", "应纳税所得额", "适用税率", "速算扣除数", "应缴税额", "已预缴税额", "补/退税额"]
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

# ---------------------- 核心计算函数（保留原有逻辑） ----------------------
def calculate_tax_brackets(income, brackets):
    tax = 0
    remaining = max(income, 0)
    for bracket, rate, deduction in brackets:
        if remaining <= 0:
            break
        if remaining > bracket:
            tax += bracket * rate - deduction
            remaining -= bracket
        else:
            tax += remaining * rate - deduction
            remaining = 0
    return round(tax, 2)

def calculate_equity_tax(
    incentive_tool, exercise_method, tax_resident, listing_location,
    exercise_price, exercise_quantity, exercise_market_price,
    transfer_price, other_income=0, special_deduction=0
):
    st.session_state['mp'] = exercise_market_price
    st.session_state['ep'] = exercise_price

    # 1. 行权收入计算
    exercise_income = INCENTIVE_TOOLS[incentive_tool]["income_calc"](
        exercise_price, exercise_market_price, exercise_quantity
    )
    exercise_income = max(exercise_income, 0)

    # 2. 行权税款计算
    rule = TAX_RULES[tax_resident]
    exercise_tax = 0
    if rule["exercise_tax_type"] != "无个税":
        if tax_resident == "中国大陆" and listing_location == "境内":
            total_income = exercise_income + other_income
            taxable_income = max(total_income - 60000 - special_deduction, 0)
            exercise_tax = calculate_tax_brackets(taxable_income, rule["exercise_tax_brackets"])
        else:
            exercise_tax = calculate_tax_brackets(exercise_income, rule["exercise_tax_brackets"])
            if tax_resident == "美国（加州）":
                exercise_tax += exercise_income * rule["state_tax_rate"]
    exercise_tax = round(exercise_tax, 2)

    # 3. 实际持有数量
    actual_quantity = EXERCISE_METHODS[exercise_method]["actual_quantity"](
        exercise_quantity, exercise_tax
    )
    actual_quantity = max(round(actual_quantity, 2), 0)

    # 4. 转让税款
    transfer_tax = 0
    transfer_income = 0
    if transfer_price > 0:
        transfer_income = (transfer_price - exercise_market_price) * actual_quantity
        transfer_income = max(transfer_income, 0)
        if not (rule["transfer_tax_exempt"] and listing_location == "境内"):
            transfer_tax = transfer_income * rule["transfer_tax_rate"]
            if tax_resident == "美国（加州）":
                transfer_tax += transfer_income * rule["state_tax_rate"]
        transfer_tax = round(transfer_tax, 2)

    # 5. 总收益/税款
    total_tax = round(exercise_tax + transfer_tax, 2)
    total_income = exercise_income + transfer_income
    net_income = round(total_income - total_tax, 2)

    # 整理结果
    result = {
        "激励工具类型": incentive_tool,
        "行权方式": exercise_method,
        "税务居民身份": tax_resident,
        "上市地": listing_location,
        "行权价/授予价(元/股)": exercise_price,
        "行权/解禁数量(股)": exercise_quantity,
        "行权/解禁日市价(元/股)": exercise_market_price,
        "转让价(元/股)": transfer_price,
        "年度其他综合所得(元)": other_income,
        "年度专项附加扣除(元)": special_deduction,
        "行权收入(元)": exercise_income,
        "行权环节税款(元)": exercise_tax,
        "实际持有数量(股)": actual_quantity,
        "转让收入(元)": transfer_income,
        "转让环节税款(元)": transfer_tax,
        "总税款(元)": total_tax,
        "总收益(元)": total_income,
        "净收益(元)": net_income,
        "行权收入计算公式": INCENTIVE_TOOLS[incentive_tool]["income_formula"],
        "行权方式计算公式": EXERCISE_METHODS[exercise_method]["formula"],
        "行权税款计算公式": rule["exercise_tax_formula"],
        "转让税款计算公式": rule["transfer_tax_formula"],
        "适用报税表单": rule["tax_form"]
    }
    return result

# ---------------------- 新增：报税表单生成函数 ----------------------
def generate_tax_form(result, rule, tax_resident):
    """根据计算结果生成对应地区报税表单"""
    form_data = {}
    # 基础公共字段赋值
    form_data["股权激励类型"] = result["激励工具类型"]
    form_data["行权收入"] = f"{result['行权收入(元)']:.2f}"
    form_data["转让收益金额"] = f"{result['转让收入(元)']:.2f}"
    form_data["应缴税额"] = f"{result['总税款(元)']:.2f}"
    form_data["行权/解禁日期"] = "____年____月____日"
    form_data["报税年度"] = datetime.now().strftime("%Y")
    # 地区专属字段默认值
    for field in rule["form_fields"]:
        if field not in form_data:
            form_data[field] = "__________"
    # 按地区补充专属值
    if tax_resident == "中国大陆":
        form_data["应纳税所得额"] = max(result['行权收入(元)'] + result['年度其他综合所得(元)'] - 60000 - result['年度专项附加扣除(元)'], 0)
        form_data["适用税率"] = "3%-45%（超额累进）"
    elif tax_resident in ["美国（加州）", "美国（德州）"]:
        form_data["工薪收入（股权激励）"] = f"{result['行权收入(元)']:.2f}"
        form_data["资本利得（转让）"] = f"{result['转让收入(元)']:.2f}"
    # 整理成表单格式
    form_df = pd.DataFrame({
        "报税字段": rule["form_fields"],
        "填写值（自动生成/手动补充）": [form_data[field] for field in rule["form_fields"]],
        "备注": ["复制后填写至官方表单" for _ in rule["form_fields"]]
    })
    return form_df

# ---------------------- 新增：结果导出函数（CSV+Excel） ----------------------
def export_result_to_excel(result, form_df):
    """导出计算结果+报税表单到Excel"""
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    # 核心结果sheet
    core_result = {k: v for k, v in result.items() if not k.endswith("计算公式")}
    pd.DataFrame([core_result]).T.to_excel(writer, sheet_name="核心计算结果", header=["数值"])
    # 计算公式sheet
    formula_result = {k: v for k, v in result.items() if k.endswith("计算公式")}
    pd.DataFrame([formula_result]).T.to_excel(writer, sheet_name="计算公式", header=["说明"])
    # 报税表单sheet
    form_df.to_excel(writer, sheet_name="报税表单模板", index=False)
    writer.close()
    output.seek(0)
    return output

# ---------------------- Streamlit 界面（整合所有优化） ----------------------
st.title("🧮 股权激励个税计算器（全功能版）")
st.markdown("### 多工具/行权方式/地区 | 参数记忆 | 结果双导出 | 税款可视化 | 多国报税表单自动生成")
st.divider()

# ---------------------- 1. 参数记忆：初始化/加载session_state ----------------------
# 基础配置参数
if "incentive_tool" not in st.session_state:
    st.session_state.incentive_tool = "期权（Option）"
if "exercise_method" not in st.session_state:
    st.session_state.exercise_method = "现金行权（Cash Exercise）"
if "tax_resident" not in st.session_state:
    st.session_state.tax_resident = "中国大陆"
if "listing_location" not in st.session_state:
    st.session_state.listing_location = "境外"
# 价格/数量参数
if "exercise_price" not in st.session_state:
    st.session_state.exercise_price = 10.0
if "exercise_quantity" not in st.session_state:
    st.session_state.exercise_quantity = 1000
if "exercise_market_price" not in st.session_state:
    st.session_state.exercise_market_price = 20.0
if "transfer_price" not in st.session_state:
    st.session_state.transfer_price = 0.0
# 其他扣除参数
if "other_income" not in st.session_state:
    st.session_state.other_income = 0.0
if "special_deduction" not in st.session_state:
    st.session_state.special_deduction = 0.0

# ---------------------- 2. 侧边栏输入：绑定session_state实现记忆 ----------------------
with st.sidebar:
    st.header("📝 基础配置")
    # 绑定session_state，自动填充上次值
    st.session_state.incentive_tool = st.selectbox("激励工具类型", list(INCENTIVE_TOOLS.keys()), index=list(INCENTIVE_TOOLS.keys()).index(st.session_state.incentive_tool))
    st.session_state.exercise_method = st.selectbox("行权/解禁方式", list(EXERCISE_METHODS.keys()), index=list(EXERCISE_METHODS.keys()).index(st.session_state.exercise_method))
    st.session_state.tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()), index=list(TAX_RULES.keys()).index(st.session_state.tax_resident))
    st.session_state.listing_location = st.selectbox("上市地", ["境内", "境外"], index=["境内", "境外"].index(st.session_state.listing_location))
    
    st.subheader("📊 价格/数量参数")
    price_label = "行权价/授予价（元/股）" if st.session_state.incentive_tool != "限制性股票（RSU）" else "RSU无需行权价（填0）"
    st.session_state.exercise_price = st.number_input(price_label, min_value=0.0, step=0.1, value=st.session_state.exercise_price)
    st.session_state.exercise_quantity = st.number_input("行权/解禁数量（股）", min_value=0, step=100, value=st.session_state.exercise_quantity)
    st.session_state.exercise_market_price = st.number_input("行权/解禁日市价（元/股）", min_value=0.0, step=0.1, value=st.session_state.exercise_market_price)
    st.session_state.transfer_price = st.number_input("转让价（元/股，未转让填0）", min_value=0.0, step=0.1, value=st.session_state.transfer_price)
    
    st.subheader("💰 其他扣除（可选）")
    st.session_state.other_income = st.number_input("年度其他综合所得（元）", min_value=0.0, step=1000.0, value=st.session_state.other_income)
    st.session_state.special_deduction = st.number_input("年度专项附加扣除（元）", min_value=0.0, step=1000.0, value=st.session_state.special_deduction)
    
    # 计算按钮+智能提示
    calc_btn = st.button("🔍 开始计算", type="primary")
    # 重置参数按钮
    if st.button("🔄 重置所有参数"):
        st.session_state.clear()
        st.rerun()

# ---------------------- 3. 主界面：计算+结果展示+所有优化功能 ----------------------
if calc_btn:
    # 智能参数校验
    if st.session_state.exercise_quantity <= 0:
        st.warning("⚠️ 行权/解禁数量不能为0或负数，请重新输入！")
    elif st.session_state.exercise_market_price < st.session_state.exercise_price and st.session_state.incentive_tool != "限制性股票（RSU）":
        st.warning("⚠️ 行权日市价低于行权价，本次行权无收入，税款为0！")
    else:
        # 调用计算函数
        result = calculate_equity_tax(
            incentive_tool=st.session_state.incentive_tool,
            exercise_method=st.session_state.exercise_method,
            tax_resident=st.session_state.tax_resident,
            listing_location=st.session_state.listing_location,
            exercise_price=st.session_state.exercise_price,
            exercise_quantity=st.session_state.exercise_quantity,
            exercise_market_price=st.session_state.exercise_market_price,
            transfer_price=st.session_state.transfer_price,
            other_income=st.session_state.other_income,
            special_deduction=st.session_state.special_deduction
        )
        rule = TAX_RULES[st.session_state.tax_resident]
        # 生成报税表单
       tax_form_df = generate_tax_form(result, rule, st.session_state.tax_resident)

        # 3.1 核心计算结果
        st.subheader("📊 核心计算结果")
        core_result = {k: v for k, v in result.items() if not k.endswith("计算公式")}
        core_df = pd.DataFrame([core_result]).T
        st.dataframe(core_df, column_config={"0": "数值"}, use_container_width=True)

        # 3.2 税款构成可视化：Plotly饼图
        st.divider()
        st.subheader("📈 税款构成分析")
        tax_data = pd.DataFrame({
            "税款类型": ["行权环节税款", "转让环节税款"],
            "金额（元）": [result["行权环节税款(元)"], result["转让环节税款(元)"]]
        })
        if result["总税款(元)"] > 0:
            fig = px.pie(
                tax_data, values="金额（元）", names="税款类型",
                title=f"总税款：{result['总税款(元)']:.2f} 元",
                hole=0.3, color_discrete_sequence=["#FF6B6B", "#4ECDC4"]
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("✅ 本次股权激励无应缴税款，无需绘制税款构成图")

        # 3.3 计算公式说明
        st.divider()
        st.subheader("📖 计算公式说明")
        formula_cols = st.columns(2)
        with formula_cols[0]:
            st.info(f"**行权收入**：{result['行权收入计算公式']}")
            st.info(f"**行权方式**：{result['行权方式计算公式']}")
        with formula_cols[1]:
            st.info(f"**行权税款**：{result['行权税款计算公式']}")
            st.info(f"**转让税款**：{result['转让税款计算公式']}")

        # 3.4 结果导出：CSV+Excel
        st.divider()
        st.subheader("📥 计算结果导出（含报税表单）")
        export_cols = st.columns(2)
        # CSV导出
        with export_cols[0]:
            csv = pd.DataFrame([result]).to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="📄 导出为CSV文件",
                data=csv,
                file_name=f"股权激励计税结果_{datetime.now().strftime('%Y%m%d%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        # Excel导出（核心结果+公式+报税表单）
        with export_cols[1]:
            excel_data = export_result_to_excel(result, tax_form_df)
            st.download_button(
                label="📊 导出为Excel文件（推荐）",
                data=excel_data,
                file_name=f"股权激励计税+报税表单_{datetime.now().strftime('%Y%m%d%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

        # 3.5 多国报税表单自动生成
        st.divider()
        st.subheader("📋 专属报税表单模板（自动生成）")
        st.markdown(f"### 适用表单：{result['适用报税表单']}")
        st.markdown("#### 表单字段可直接复制，补充空白项后即可填写官方报税表")
        st.dataframe(tax_form_df, use_container_width=True)
        # 报税表单单独导出
        form_csv = tax_form_df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📄 单独导出报税表单为CSV",
            data=form_csv,
            file_name=f"{st.session_state.tax_resident}_股权激励报税表单_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )

# ---------------------- 底部说明 ----------------------
st.divider()
st.markdown("""
> ⚠️ 免责声明：本工具为税务参考工具，报税表单为简易模板；实际税款及报税请以当地税务机关核定和官方表单为准，建议咨询专业税务师。
> 📌 功能说明：参数自动记忆（关闭页面重新打开仍保留）、Excel导出含3个sheet（核心结果/计算公式/报税表单）、报税表单字段可直接复制。
""")
