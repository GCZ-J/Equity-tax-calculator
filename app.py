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
    page_title="股权激励个税计算器（多记录版）",
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

# 3. 多地区税务规则（含报税表单，已修改中国大陆A/B表逻辑）
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
        "tax_form_A": "个人所得税综合所得年度汇算申报表（A表）",  # 境内收入用A表
        "tax_form_B": "个人所得税综合所得年度汇算申报表（B表）",  # 境外收入用B表
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

# ---------------------- 核心计算函数（适配单条记录） ----------------------
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

def calculate_single_equity(
    record_id, incentive_tool, exercise_method, tax_resident, listing_location,
    exercise_price, exercise_quantity, exercise_market_price,
    transfer_price, other_income=0, special_deduction=0
):
    """计算单条股权激励记录的结果"""
    mp = exercise_market_price
    ep = exercise_price

    # 1. 行权收入计算
    exercise_income = INCENTIVE_TOOLS[incentive_tool]["income_calc"](ep, mp, exercise_quantity)
    exercise_income = max(exercise_income, 0)

    # 2. 行权税款计算
    rule = TAX_RULES[tax_resident]
    exercise_tax = 0
    if rule["exercise_tax_type"] != "无个税":
        # 注意：综合所得的专项扣除是全局的，单条记录暂不计入，汇总时统一计算
        exercise_tax = calculate_tax_brackets(exercise_income, rule["exercise_tax_brackets"])
        if tax_resident == "美国（加州）":
            exercise_tax += exercise_income * rule["state_tax_rate"]
    exercise_tax = round(exercise_tax, 2)

    # 3. 实际持有数量
    actual_quantity = EXERCISE_METHODS[exercise_method]["actual_quantity"](exercise_quantity, exercise_tax)
    actual_quantity = max(round(actual_quantity, 2), 0)

    # 4. 转让税款
    transfer_tax = 0
    transfer_income = 0
    if transfer_price > 0:
        transfer_income = (transfer_price - mp) * actual_quantity
        transfer_income = max(transfer_income, 0)
        if not (rule["transfer_tax_exempt"] and listing_location == "境内"):
            transfer_tax = transfer_income * rule["transfer_tax_rate"]
            if tax_resident == "美国（加州）":
                transfer_tax += transfer_income * rule["state_tax_rate"]
        transfer_tax = round(transfer_tax, 2)

    # 5. 单条收益/税款
    total_tax = round(exercise_tax + transfer_tax, 2)
    total_income = exercise_income + transfer_income
    net_income = round(total_income - total_tax, 2)

    # 整理单条结果
    result = {
        "记录ID": record_id,
        "激励工具类型": incentive_tool,
        "行权方式": exercise_method,
        "行权价/授予价(元/股)": exercise_price,
        "行权/解禁数量(股)": exercise_quantity,
        "行权/解禁日市价(元/股)": exercise_market_price,
        "转让价(元/股)": transfer_price,
        "行权收入(元)": exercise_income,
        "行权环节税款(元)": exercise_tax,
        "实际持有数量(股)": actual_quantity,
        "转让收入(元)": transfer_income,
        "转让环节税款(元)": transfer_tax,
        "单条总税款(元)": total_tax,
        "单条总收益(元)": total_income,
        "单条净收益(元)": net_income,
        "行权收入计算公式": INCENTIVE_TOOLS[incentive_tool]["income_formula"],
        "行权方式计算公式": EXERCISE_METHODS[exercise_method]["formula"]
    }
    return result

def calculate_summary_results(detail_results, tax_resident, listing_location, other_income, special_deduction):
    """汇总所有记录的结果（含综合所得专项扣除，新增listing_location参数）"""
    # 1. 基础汇总
    total_exercise_income = sum([r["行权收入(元)"] for r in detail_results])
    total_transfer_income = sum([r["转让收入(元)"] for r in detail_results])
    total_exercise_tax = sum([r["行权环节税款(元)"] for r in detail_results])
    total_transfer_tax = sum([r["转让环节税款(元)"] for r in detail_results])
    total_tax = round(total_exercise_tax + total_transfer_tax, 2)
    total_income = round(total_exercise_income + total_transfer_income, 2)
    net_income = round(total_income - total_tax, 2)

    # 2. 适配中国大陆综合所得（专项扣除+A/B表判断）
    if tax_resident == "中国大陆":
        taxable_income = max(total_exercise_income + other_income - 60000 - special_deduction, 0)
        rule = TAX_RULES[tax_resident]
        total_exercise_tax = calculate_tax_brackets(taxable_income, rule["exercise_tax_brackets"])
        total_tax = round(total_exercise_tax + total_transfer_tax, 2)
        net_income = round(total_income - total_tax, 2)
        # 新增：根据上市地确定A/B表
        if listing_location == "境内":
            tax_form = rule["tax_form_A"]
        else:
            tax_form = rule["tax_form_B"]
    else:
        tax_form = TAX_RULES[tax_resident]["tax_form"]

    # 整理汇总结果
    summary = {
        "税务居民身份": tax_resident,
        "上市地": listing_location,
        "年度其他综合所得(元)": other_income,
        "年度专项附加扣除(元)": special_deduction,
        "汇总行权收入(元)": total_exercise_income,
        "汇总转让收入(元)": total_transfer_income,
        "汇总行权环节税款(元)": total_exercise_tax,
        "汇总转让环节税款(元)": total_transfer_tax,
        "汇总总税款(元)": total_tax,
        "汇总总收益(元)": total_income,
        "汇总净收益(元)": net_income,
        "适用报税表单": tax_form
    }
    return summary

# ---------------------- 报税表单生成函数（已关联上市地判断A/B表） ----------------------
def generate_tax_form(summary, tax_resident):
    """根据汇总结果生成报税表单"""
    rule = TAX_RULES[tax_resident]
    form_data = {}
    # 基础字段赋值
    form_data["股权激励类型"] = "多种激励工具汇总"
    form_data["行权收入"] = f"{summary['汇总行权收入(元)']:.2f}"
    form_data["转让收益金额"] = f"{summary['汇总转让收入(元)']:.2f}"
    form_data["应缴税额"] = f"{summary['汇总总税款(元)']:.2f}"
    form_data["行权/解禁日期"] = "____年____月____日（汇总）"
    form_data["报税年度"] = datetime.now().strftime("%Y")
    # 地区专属字段
    for field in rule["form_fields"]:
        if field not in form_data:
            form_data[field] = "__________"
    # 补充中国大陆专属值
    if tax_resident == "中国大陆":
        form_data["应纳税所得额"] = max(summary['汇总行权收入(元)'] + summary['年度其他综合所得(元)'] - 60000 - summary['年度专项附加扣除(元)'], 0)
        form_data["适用税率"] = "3%-45%（超额累进）"
    elif tax_resident in ["美国（加州）", "美国（德州）"]:
        form_data["工薪收入（股权激励）"] = f"{summary['汇总行权收入(元)']:.2f}"
        form_data["资本利得（转让）"] = f"{summary['汇总转让收入(元)']:.2f}"
    # 整理表单
    form_df = pd.DataFrame({
        "报税字段": rule["form_fields"],
        "填写值（自动生成/手动补充）": [form_data[field] for field in rule["form_fields"]],
        "备注": ["复制后填写至官方表单" for _ in rule["form_fields"]]
    })
    return form_df

# ---------------------- 结果导出函数（适配明细+汇总） ----------------------
def export_result_to_excel(detail_results, summary, form_df):
    """导出明细+汇总+报税表单到Excel"""
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    # 1. 明细结果sheet
    detail_df = pd.DataFrame(detail_results)
    detail_df.to_excel(writer, sheet_name="单条明细结果", index=False)
    # 2. 汇总结果sheet
    summary_df = pd.DataFrame([summary])
    summary_df.to_excel(writer, sheet_name="汇总结果", index=False)
    # 3. 报税表单sheet
    form_df.to_excel(writer, sheet_name="报税表单模板", index=False)
    writer.close()
    output.seek(0)
    return output

# ---------------------- Streamlit 界面（多记录版） ----------------------
st.title("🧮 股权激励个税计算器（多记录批量版）")
st.markdown("### 支持多重激励工具/行权价格/转让价格 | 明细+汇总计算 | 结果导出 | 税款可视化")
st.divider()

# ---------------------- 1. 全局参数初始化（记忆） ----------------------
# 全局参数（所有记录共用）
if "tax_resident" not in st.session_state:
    st.session_state.tax_resident = "中国大陆"
if "listing_location" not in st.session_state:
    st.session_state.listing_location = "境外"
if "exercise_method" not in st.session_state:
    st.session_state.exercise_method = "现金行权（Cash Exercise）"
if "other_income" not in st.session_state:
    st.session_state.other_income = 0.0
if "special_deduction" not in st.session_state:
    st.session_state.special_deduction = 0.0

# 多记录存储（列表，每条是字典）
if "equity_records" not in st.session_state:
    st.session_state.equity_records = [
        # 初始默认1条记录
        {
            "id": 1,
            "incentive_tool": "期权（Option）",
            "exercise_price": 10.0,
            "exercise_quantity": 1000,
            "exercise_market_price": 20.0,
            "transfer_price": 0.0
        }
    ]

# ---------------------- 2. 侧边栏：全局参数 + 记录操作 ----------------------
with st.sidebar:
    st.header("🌐 全局参数（所有记录共用）")
    # 全局参数输入（记忆）
    st.session_state.tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()), index=list(TAX_RULES.keys()).index(st.session_state.tax_resident))
    st.session_state.listing_location = st.selectbox("上市地", ["境内", "境外"], index=["境内", "境外"].index(st.session_state.listing_location))
    st.session_state.exercise_method = st.selectbox("行权/解禁方式", list(EXERCISE_METHODS.keys()), index=list(EXERCISE_METHODS.keys()).index(st.session_state.exercise_method))
    
    st.subheader("💰 年度扣除项（仅中国大陆适用）")
    st.session_state.other_income = st.number_input("年度其他综合所得(元)", min_value=0.0, step=1000.0, value=st.session_state.other_income)
    st.session_state.special_deduction = st.number_input("年度专项附加扣除(元)", min_value=0.0, step=1000.0, value=st.session_state.special_deduction)

    st.divider()
    st.header("📝 记录操作")
    # 添加/删除记录按钮
    col_add, col_del = st.columns(2)
    with col_add:
        if st.button("➕ 添加一条记录", type="primary"):
            new_id = len(st.session_state.equity_records) + 1
            st.session_state.equity_records.append({
                "id": new_id,
                "incentive_tool": "期权（Option）",
                "exercise_price": 10.0,
                "exercise_quantity": 1000,
                "exercise_market_price": 20.0,
                "transfer_price": 0.0
            })
    with col_del:
        if st.button("➖ 删除最后一条", disabled=len(st.session_state.equity_records) <= 1):
            st.session_state.equity_records.pop()
    
    # 重置按钮
    if st.button("🔄 重置所有参数"):
        st.session_state.clear()
        st.rerun()

    # 计算按钮
    calc_btn = st.button("📊 批量计算", type="secondary", use_container_width=True)

# ---------------------- 3. 主界面：动态多行输入（每条记录） ----------------------
st.subheader("📋 股权激励记录列表（可添加/删除）")
st.markdown("#### 每条记录可独立设置激励工具、行权价、数量等参数")

# 循环生成每条记录的输入框
for idx, record in enumerate(st.session_state.equity_records):
    with st.expander(f"记录 {record['id']}", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            record["incentive_tool"] = st.selectbox(
                "激励工具类型", list(INCENTIVE_TOOLS.keys()),
                index=list(INCENTIVE_TOOLS.keys()).index(record["incentive_tool"]),
                key=f"tool_{record['id']}"
            )
            price_label = "行权价/授予价(元/股)" if record["incentive_tool"] != "限制性股票（RSU）" else "RSU无需行权价（填0）"
            record["exercise_price"] = st.number_input(
                price_label, min_value=0.0, step=0.1, value=record["exercise_price"],
                key=f"price_{record['id']}"
            )
        with col2:
            record["exercise_quantity"] = st.number_input(
                "行权/解禁数量(股)", min_value=0, step=100, value=record["exercise_quantity"],
                key=f"qty_{record['id']}"
            )
            record["exercise_market_price"] = st.number_input(
                "行权/解禁日市价(元/股)", min_value=0.0, step=0.1, value=record["exercise_market_price"],
                key=f"mp_{record['id']}"
            )
        with col3:
            record["transfer_price"] = st.number_input(
                "转让价(元/股，未转让填0)", min_value=0.0, step=0.1, value=record["transfer_price"],
                key=f"tp_{record['id']}"
            )
    st.divider()

# ---------------------- 4. 批量计算 + 结果展示 ----------------------
if calc_btn:
    # 1. 校验所有记录
    valid_records = []
    for record in st.session_state.equity_records:
        if record["exercise_quantity"] <= 0:
            st.warning(f"⚠️ 记录 {record['id']}：行权数量不能为0或负数！")
        elif record["exercise_market_price"] < record["exercise_price"] and record["incentive_tool"] != "限制性股票（RSU）":
            st.warning(f"⚠️ 记录 {record['id']}：市价低于行权价，行权收入为0（不影响计算）")
            valid_records.append(record)
        else:
            valid_records.append(record)
    
    if not valid_records:
        st.error("❌ 无有效记录，请检查输入！")
    else:
        # 2. 计算每条记录的明细结果
        detail_results = []
        for record in valid_records:
            single_result = calculate_single_equity(
                record_id=record["id"],
                incentive_tool=record["incentive_tool"],
                exercise_method=st.session_state.exercise_method,
                tax_resident=st.session_state.tax_resident,
                listing_location=st.session_state.listing_location,
                exercise_price=record["exercise_price"],
                exercise_quantity=record["exercise_quantity"],
                exercise_market_price=record["exercise_market_price"],
                transfer_price=record["transfer_price"],
                other_income=st.session_state.other_income,
                special_deduction=st.session_state.special_deduction
            )
            detail_results.append(single_result)
        
        # 3. 计算汇总结果（传入listing_location参数）
        summary = calculate_summary_results(
            detail_results,
            tax_resident=st.session_state.tax_resident,
            listing_location=st.session_state.listing_location,
            other_income=st.session_state.other_income,
            special_deduction=st.session_state.special_deduction
        )
        
        # 4. 生成报税表单
        tax_form_df = generate_tax_form(summary, st.session_state.tax_resident)

        # ---------------------- 结果展示 ----------------------
        st.success("✅ 批量计算完成！以下是明细+汇总结果")
        
        # 4.1 单条明细结果
        st.subheader("📈 单条记录明细结果")
        detail_df = pd.DataFrame(detail_results)
        # 隐藏冗余字段，只展示核心列
        show_cols = ["记录ID", "激励工具类型", "行权价/授予价(元/股)", "行权/解禁数量(股)", 
                    "行权收入(元)", "转让收入(元)", "单条总税款(元)", "单条净收益(元)"]
        st.dataframe(detail_df[show_cols], use_container_width=True)

        # 4.2 汇总结果
        st.subheader("📊 所有记录汇总结果")
        summary_df = pd.DataFrame([summary])
        st.dataframe(summary_df, use_container_width=True)

        # 4.3 税款构成可视化（汇总）
        st.subheader("📉 汇总税款构成分析")
        tax_data = pd.DataFrame({
            "税款类型": ["行权环节税款", "转让环节税款"],
            "金额（元）": [summary["汇总行权环节税款(元)"], summary["汇总转让环节税款(元)"]]
        })
        if summary["汇总总税款(元)"] > 0:
            fig = px.pie(
                tax_data, values="金额（元）", names="税款类型",
                title=f"汇总总税款：{summary['汇总总税款(元)']:.2f} 元",
                hole=0.3, color_discrete_sequence=["#FF6B6B", "#4ECDC4"]
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("✅ 本次股权激励无应缴税款")

        # 4.4 报税表单
        st.subheader("📋 汇总报税表单模板")
        st.markdown(f"### 适用表单：{summary['适用报税表单']}")
        st.dataframe(tax_form_df, use_container_width=True)

        # 4.5 结果导出（明细+汇总+报税表单）
        st.subheader("📥 结果导出（Excel/CSV）")
        col_export1, col_export2 = st.columns(2)
        with col_export1:
            # 导出Excel（推荐）
            excel_data = export_result_to_excel(detail_results, summary, tax_form_df)
            st.download_button(
                label="📊 导出Excel（明细+汇总+报税表单）",
                data=excel_data,
                file_name=f"股权激励批量计算结果_{datetime.now().strftime('%Y%m%d%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        with col_export2:
            # 导出CSV（明细）
            csv_data = detail_df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="📄 导出CSV（单条明细）",
                data=csv_data,
                file_name=f"股权激励明细结果_{datetime.now().strftime('%Y%m%d%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )

# ---------------------- 免责声明 ----------------------
st.divider()
st.markdown("""
> ⚠️ 免责声明：本工具为税务参考工具，报税表单为简易模板；实际税款及报税请以当地税务机关核定和官方表单为准，建议咨询专业税务师。
> 📌 功能说明：支持多记录批量计算、明细+汇总展示、Excel/CSV导出、税款可视化。
""")
