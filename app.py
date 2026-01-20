import streamlit as st
import pandas as pd

# ---------------------- 页面基础配置 ----------------------
st.set_page_config(
    page_title="股权激励个税计算器（多地区）",
    page_icon="🧮",
    layout="centered"
)

# ---------------------- 多地区税率规则配置（核心） ----------------------
TAX_RULES = {
    "中国大陆": {
        "exercise_tax_type": "综合所得",
        "exercise_tax_brackets": [
            (36000, 0.03, 0),
            (144000, 0.1, 2520),
            (300000, 0.2, 16920),
            (420000, 0.25, 31920),
            (660000, 0.3, 52920),
            (960000, 0.35, 85920),
            (float('inf'), 0.45, 181920)
        ],
        "transfer_tax_rate": 0.2,  # 境外转让20%，境内0%
        "transfer_tax_exempt": True,  # 境内上市转让免税
        "description": "行权收入：境内上市并入综合所得（扣6万起征点），境外上市可单独计税；转让收入：境内免税，境外按20%计税"
    },
    "中国香港": {
        "exercise_tax_type": "薪俸税",
        "exercise_tax_brackets": [
            (50000, 0.02, 0),
            (50000, 0.06, 1000),
            (50000, 0.1, 3000),
            (50000, 0.14, 5000),
            (float('inf'), 0.17, 7000)
        ],
        "transfer_tax_rate": 0.0,  # 香港无资本利得税
        "transfer_tax_exempt": True,
        "description": "行权收入按薪俸税计税（免税额132000港币/年，此处简化为0）；转让收入无资本利得税"
    },
    "新加坡": {
        "exercise_tax_type": "个人所得税",
        "exercise_tax_brackets": [
            (20000, 0.02, 0),
            (10000, 0.035, 400),
            (10000, 0.07, 750),
            (40000, 0.115, 1150),
            (40000, 0.15, 2750),
            (40000, 0.18, 4750),
            (40000, 0.19, 6550),
            (40000, 0.2, 8150),
            (float('inf'), 0.22, 8950)
        ],
        "transfer_tax_rate": 0.0,  # 新加坡无资本利得税
        "transfer_tax_exempt": True,
        "description": "行权收入并入薪资按个人所得税计税；转让收入无资本利得税"
    },
    "阿联酋": {
        "exercise_tax_type": "无个税",
        "exercise_tax_brackets": [(float('inf'), 0.0, 0)],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "description": "阿联酋无个人所得税，行权和转让收入均免税"
    },
    "德国": {
        "exercise_tax_type": "所得税",
        "exercise_tax_brackets": [
            (9984, 0.0, 0),
            (8632, 0.14, 0),
            (107394, 0.42, 950.96),
            (float('inf'), 0.45, 3666.84)
        ],
        "transfer_tax_rate": 0.25,  # 资本利得税25%（含团结税）
        "transfer_tax_exempt": False,
        "description": "行权收入按所得税14%-45%计税；转让收入按25%（含团结税）计税"
    },
    "法国": {
        "exercise_tax_type": "所得税",
        "exercise_tax_brackets": [
            (11294, 0.0, 0),
            (28797, 0.11, 0),
            (28797, 0.3, 3167.67),
            (75550, 0.41, 11706.78),
            (float('inf'), 0.45, 14728.78)
        ],
        "transfer_tax_rate": 0.30,  # 资本利得税30%（含社会捐税）
        "transfer_tax_exempt": False,
        "description": "行权收入按所得税0%-45%计税；转让收入按30%（含社会捐税）计税"
    },
    "美国（加州）": {
        "exercise_tax_type": "联邦+州税",
        "exercise_tax_brackets": [
            (11600, 0.10, 0),
            (47150, 0.12, 1160),
            (100525, 0.22, 5928),
            (191950, 0.24, 17602),
            (243725, 0.32, 34648),
            (609350, 0.35, 47836),
            (float('inf'), 0.37, 65469)
        ],
        "state_tax_rate": 0.123,  # 加州州税最高12.3%
        "transfer_tax_rate": 0.20,  # 联邦资本利得税20%
        "transfer_tax_exempt": False,
        "description": "行权收入：联邦税10%-37% + 加州州税12.3%；转让收入：联邦资本利得税20% + 加州州税12.3%"
    },
    "美国（德州）": {
        "exercise_tax_type": "联邦税（无州税）",
        "exercise_tax_brackets": [
            (11600, 0.10, 0),
            (47150, 0.12, 1160),
            (100525, 0.22, 5928),
            (191950, 0.24, 17602),
            (243725, 0.32, 34648),
            (609350, 0.35, 47836),
            (float('inf'), 0.37, 65469)
        ],
        "state_tax_rate": 0.0,  # 德州无州税
        "transfer_tax_rate": 0.20,  # 联邦资本利得税20%
        "transfer_tax_exempt": False,
        "description": "行权收入：仅联邦税10%-37%（无州税）；转让收入：仅联邦资本利得税20%（无州税）"
    }
}

# ---------------------- 核心计税函数 ----------------------
def calculate_tax_brackets(income, brackets):
    """按税率表计算税款"""
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
    tax_resident,
    listing_location,
    exercise_price,
    exercise_quantity,
    exercise_market_price,
    transfer_price,
    other_income=0,
    special_deduction=0
):
    """多地区股权激励计税核心函数"""
    # 1. 行权收入
    exercise_income = (exercise_market_price - exercise_price) * exercise_quantity
    exercise_income = max(exercise_income, 0)

    # 2. 行权税款（按地区规则）
    rule = TAX_RULES[tax_resident]
    exercise_tax = 0
    if rule["exercise_tax_type"] != "无个税":
        if tax_resident == "中国大陆" and listing_location == "境内":
            # 中国大陆境内上市：并入综合所得（扣6万+专项扣除）
            total_income = exercise_income + other_income
            taxable_income = max(total_income - 60000 - special_deduction, 0)
            exercise_tax = calculate_tax_brackets(taxable_income, rule["exercise_tax_brackets"])
        else:
            # 其他地区/中国大陆境外上市：单独计税
            exercise_tax = calculate_tax_brackets(exercise_income, rule["exercise_tax_brackets"])
            # 美国加州加征州税
            if tax_resident == "美国（加州）":
                exercise_tax += exercise_income * rule["state_tax_rate"]

    # 3. 转让税款
    transfer_tax = 0
    if transfer_price > 0:
        transfer_income = (transfer_price - exercise_market_price) * exercise_quantity
        transfer_income = max(transfer_income, 0)
        if not (rule["transfer_tax_exempt"] and listing_location == "境内"):
            transfer_tax = transfer_income * rule["transfer_tax_rate"]
            # 美国加州转让加征州税
            if tax_resident == "美国（加州）":
                transfer_tax += transfer_income * rule["state_tax_rate"]
        transfer_tax = round(transfer_tax, 2)

    # 4. 总税款和净收益
    total_tax = round(exercise_tax + transfer_tax, 2)
    total_income = exercise_income + (max(transfer_price - exercise_market_price, 0) * exercise_quantity if transfer_price > 0 else 0)
    net_income = round(total_income - total_tax, 2)

    return {
        "行权收入(元)": exercise_income,
        "行权环节税款(元)": exercise_tax,
        "转让环节税款(元)": transfer_tax,
        "总税款(元)": total_tax,
        "总收益(元)": total_income,
        "净收益(元)": net_income,
        "计税规则说明": rule["description"]
    }

# ---------------------- Streamlit 交互界面 ----------------------
st.title("🧮 股权激励个税计算器（多地区适配）")
st.markdown("### 支持：中国大陆/香港、新加坡、阿联酋、德国、法国、美国各州")
st.divider()

# 侧边栏输入
with st.sidebar:
    st.header("📝 输入计算参数")
    tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()))
    listing_location = st.selectbox("股权激励上市地", ["境内", "境外"])
    
    st.subheader("行权信息")
    exercise_price = st.number_input("行权价（元/股）", min_value=0.0, step=0.1, value=10.0)
    exercise_quantity = st.number_input("行权数量（股）", min_value=0, step=100, value=1000)
    exercise_market_price = st.number_input("行权日市价（元/股）", min_value=0.0, step=0.1, value=20.0)
    
    st.subheader("转让信息（未转让填0）")
    transfer_price = st.number_input("转让价（元/股）", min_value=0.0, step=0.1, value=0.0)
    
    st.subheader("其他扣除（可选）")
    other_income = st.number_input("年度其他综合所得（元）", min_value=0.0, step=1000.0, value=0.0)
    special_deduction = st.number_input("年度专项附加扣除（元）", min_value=0.0, step=1000.0, value=0.0)
    
    calc_btn = st.button("🔍 开始计算", type="primary")

# 主界面结果展示
if calc_btn:
    result = calculate_equity_tax(
        tax_resident=tax_resident,
        listing_location=listing_location,
        exercise_price=exercise_price,
        exercise_quantity=exercise_quantity,
        exercise_market_price=exercise_market_price,
        transfer_price=transfer_price,
        other_income=other_income,
        special_deduction=special_deduction
    )
    
    # 展示结果表格
    st.subheader("📊 计算结果")
    result_df = pd.DataFrame([{k: v for k, v in result.items() if k != "计税规则说明"}]).T
    st.dataframe(result_df, column_config={"0": "金额（元）"}, use_container_width=True)
    
    # 展示计税规则
    st.divider()
    st.subheader("📋 计税规则说明")
    st.info(result["计税规则说明"])

# 免责声明
st.divider()
st.markdown("> ⚠️ 免责声明：本工具为参考版，实际税款请以当地税务机关核定为准，建议咨询专业税务师。")
