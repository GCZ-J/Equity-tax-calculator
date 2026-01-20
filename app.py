import streamlit as st
import pandas as pd

# ---------------------- 页面基础配置 ----------------------
st.set_page_config(
    page_title="股权激励个税计算器（全场景优化版）",
    page_icon="🧮",
    layout="centered"
)

# ---------------------- 核心规则配置（整合所有优化需求） ----------------------
# 1. 激励工具规则（应纳税所得额计算方式）
INCENTIVE_TOOLS = {
    "期权（Option）": {
        "income_formula": "行权收入 =（行权日市价 - 行权价）× 实际行权数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    },
    "限制性股票（RSU）": {
        "income_formula": "行权/解禁收入 = 解禁日市价 × 解禁数量（无行权价）",
        "income_calc": lambda ep, mp, q, *args: mp * q  # RSU无行权价，ep传0即可
    },
    "股票增值权（SAR）": {
        "income_formula": "行权收入 =（行权日市价 - 授予价）× 行权数量（现金结算）",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    }
}

# 2. 行权方式规则（影响实际行权数量/缴税方式）
EXERCISE_METHODS = {
    "现金行权（Cash Exercise）": {
        "desc": "以现金支付行权价，全额持有股票",
        "actual_quantity": lambda q, tax: q,  # 实际持有数量=全部行权数量
        "tax_base": lambda income: income,    # 计税基数=全部行权收入
        "formula": "实际持有数量=行权数量；计税基数=全额行权收入"
    },
    "卖股缴税（Sell to Cover）": {
        "desc": "卖出部分股票支付税款，剩余股票持有",
        "actual_quantity": lambda q, tax: q - (tax / (st.session_state.get('mp', 0) or 1)),  # 卖出缴税股票数=税款/市价
        "tax_base": lambda income: income,    # 计税基数=全部行权收入
        "formula": "实际持有数量=行权数量 - （税款÷行权日市价）；计税基数=全额行权收入"
    },
    "无现金行权（Cashless Hold）": {
        "desc": "券商垫付行权价，卖出部分股票偿还，剩余持有",
        "actual_quantity": lambda q, tax: q - ((st.session_state.get('ep', 0)*q + tax) / (st.session_state.get('mp', 0) or 1)),  # 卖出=（行权总价+税款）/市价
        "tax_base": lambda income: income,    # 计税基数=全部行权收入
        "formula": "实际持有数量=行权数量 - （行权总价+税款）÷行权日市价；计税基数=全额行权收入"
    }
}

# 3. 多地区税务规则（保留原有逻辑）
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
        "transfer_tax_rate": 0.2,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=（行权收入+其他综合所得-60000-专项附加扣除）×对应税率-速算扣除数（境内上市）；行权税款=行权收入×对应税率-速算扣除数（境外上市）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×20%（境外上市）；境内上市转让税款=0"
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
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数（薪俸税，免税额简化为0）",
        "transfer_tax_formula": "转让税款=0（香港无资本利得税）"
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
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数",
        "transfer_tax_formula": "转让税款=0（新加坡无资本利得税）"
    },
    "阿联酋": {
        "exercise_tax_type": "无个税",
        "exercise_tax_brackets": [(float('inf'), 0.0, 0)],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True,
        "exercise_tax_formula": "行权税款=0（阿联酋无个人所得税）",
        "transfer_tax_formula": "转让税款=0（阿联酋无资本利得税）"
    },
    "德国": {
        "exercise_tax_type": "所得税",
        "exercise_tax_brackets": [
            (9984, 0.0, 0),
            (8632, 0.14, 0),
            (107394, 0.42, 950.96),
            (float('inf'), 0.45, 3666.84)
        ],
        "transfer_tax_rate": 0.25,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数（所得税14%-45%）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×25%（含团结税）"
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
        "transfer_tax_rate": 0.30,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=行权收入×对应税率-速算扣除数（所得税0%-45%）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×30%（含社会捐税）"
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
        "state_tax_rate": 0.123,
        "transfer_tax_rate": 0.20,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=（行权收入×联邦税率-速算扣除数）+（行权收入×加州州税12.3%）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×（联邦20%+加州12.3%）"
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
        "state_tax_rate": 0.0,
        "transfer_tax_rate": 0.20,
        "transfer_tax_exempt": False,
        "exercise_tax_formula": "行权税款=行权收入×联邦税率-速算扣除数（无州税）",
        "transfer_tax_formula": "转让税款=（转让价-行权日市价）×实际持有数量×联邦20%（无州税）"
    }
}

# ---------------------- 核心计算函数 ----------------------
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
    incentive_tool,       # 激励工具类型
    exercise_method,      # 行权方式
    tax_resident,         # 税务居民
    listing_location,     # 上市地
    exercise_price,       # 行权价/授予价（元/股）
    exercise_quantity,    # 行权数量（股）
    exercise_market_price,# 行权/解禁日市价（元/股）
    transfer_price,       # 转让价（元/股）
    other_income=0,       # 其他综合所得
    special_deduction=0   # 专项附加扣除
):
    # 缓存市价和行权价（用于行权方式计算）
    st.session_state['mp'] = exercise_market_price
    st.session_state['ep'] = exercise_price

    # 1. 计算行权收入（按激励工具规则）
    exercise_income = INCENTIVE_TOOLS[incentive_tool]["income_calc"](
        exercise_price, exercise_market_price, exercise_quantity
    )
    exercise_income = max(exercise_income, 0)

    # 2. 计算行权税款（按地区规则）
    rule = TAX_RULES[tax_resident]
    exercise_tax = 0
    if rule["exercise_tax_type"] != "无个税":
        if tax_resident == "中国大陆" and listing_location == "境内":
            total_income = exercise_income + other_income
            taxable_income = max(total_income - 60000 - special_deduction, 0)
            exercise_tax = calculate_tax_brackets(taxable_income, rule["exercise_tax_brackets"])
        else:
            exercise_tax = calculate_tax_brackets(exercise_income, rule["exercise_tax_brackets"])
            # 美国加州加征州税
            if tax_resident == "美国（加州）":
                exercise_tax += exercise_income * rule["state_tax_rate"]
    exercise_tax = round(exercise_tax, 2)

    # 3. 计算实际持有数量（按行权方式规则）
    actual_quantity = EXERCISE_METHODS[exercise_method]["actual_quantity"](
        exercise_quantity, exercise_tax
    )
    actual_quantity = max(round(actual_quantity, 2), 0)  # 数量不能为负

    # 4. 计算转让税款
    transfer_tax = 0
    transfer_income = 0
    if transfer_price > 0:
        transfer_income = (transfer_price - exercise_market_price) * actual_quantity
        transfer_income = max(transfer_income, 0)
        if not (rule["transfer_tax_exempt"] and listing_location == "境内"):
            transfer_tax = transfer_income * rule["transfer_tax_rate"]
            # 美国加州转让加征州税
            if tax_resident == "美国（加州）":
                transfer_tax += transfer_income * rule["state_tax_rate"]
        transfer_tax = round(transfer_tax, 2)

    # 5. 总税款和净收益
    total_tax = round(exercise_tax + transfer_tax, 2)
    total_income = exercise_income + transfer_income
    net_income = round(total_income - total_tax, 2)

    # 整理结果（含计算公式）
    result = {
        # 基础结果
        "激励工具类型": incentive_tool,
        "行权方式": exercise_method,
        "行权收入(元)": exercise_income,
        "行权环节税款(元)": exercise_tax,
        "实际持有数量(股)": actual_quantity,
        "转让收入(元)": transfer_income,
        "转让环节税款(元)": transfer_tax,
        "总税款(元)": total_tax,
        "总收益(元)": total_income,
        "净收益(元)": net_income,
        # 计算公式
        "行权收入计算公式": INCENTIVE_TOOLS[incentive_tool]["income_formula"],
        "行权方式计算公式": EXERCISE_METHODS[exercise_method]["formula"],
        "行权税款计算公式": rule["exercise_tax_formula"],
        "转让税款计算公式": rule["transfer_tax_formula"]
    }
    return result

# ---------------------- Streamlit 交互界面 ----------------------
st.title("🧮 股权激励个税计算器（全场景优化版）")
st.markdown("### 支持：多激励工具+多行权方式+多地区税务规则 | 附完整计算公式")
st.divider()

# 侧边栏输入（新增激励工具、行权方式选项）
with st.sidebar:
    st.header("📝 基础配置")
    incentive_tool = st.selectbox("激励工具类型", list(INCENTIVE_TOOLS.keys()))
    exercise_method = st.selectbox("行权/解禁方式", list(EXERCISE_METHODS.keys()))
    tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()))
    listing_location = st.selectbox("上市地", ["境内", "境外"])
    
    st.subheader("📊 价格/数量参数")
    # 适配不同激励工具的参数名称
    price_label = "行权价/授予价（元/股）" if incentive_tool != "限制性股票（RSU）" else "RSU无需行权价（填0）"
    exercise_price = st.number_input(price_label, min_value=0.0, step=0.1, value=10.0 if incentive_tool != "限制性股票（RSU）" else 0.0)
    exercise_quantity = st.number_input("行权/解禁数量（股）", min_value=0, step=100, value=1000)
    exercise_market_price = st.number_input("行权/解禁日市价（元/股）", min_value=0.0, step=0.1, value=20.0)
    transfer_price = st.number_input("转让价（元/股，未转让填0）", min_value=0.0, step=0.1, value=0.0)
    
    st.subheader("💰 其他扣除（可选）")
    other_income = st.number_input("年度其他综合所得（元）", min_value=0.0, step=1000.0, value=0.0)
    special_deduction = st.number_input("年度专项附加扣除（元）", min_value=0.0, step=1000.0, value=0.0)
    
    calc_btn = st.button("🔍 开始计算", type="primary")

# 主界面结果展示（新增计算公式列）
if calc_btn:
    result = calculate_equity_tax(
        incentive_tool=incentive_tool,
        exercise_method=exercise_method,
        tax_resident=tax_resident,
        listing_location=listing_location,
        exercise_price=exercise_price,
        exercise_quantity=exercise_quantity,
        exercise_market_price=exercise_market_price,
        transfer_price=transfer_price,
        other_income=other_income,
        special_deduction=special_deduction
    )
    
    # 1. 展示核心计算结果
    st.subheader("📊 核心计算结果")
    core_result = {k: v for k, v in result.items() if not k.endswith("计算公式")}
    core_df = pd.DataFrame([core_result]).T
    st.dataframe(core_df, column_config={"0": "数值"}, use_container_width=True)
    
    # 2. 展示计算公式（醒目提示）
    st.divider()
    st.subheader("📖 计算公式说明")
    formula_cols = st.columns(2)
    with formula_cols[0]:
        st.info(f"**行权收入**：{result['行权收入计算公式']}")
        st.info(f"**行权方式**：{result['行权方式计算公式']}")
    with formula_cols[1]:
        st.info(f"**行权税款**：{result['行权税款计算公式']}")
        st.info(f"**转让税款**：{result['转让税款计算公式']}")
    
    # 3. 行权方式补充说明
    st.divider()
    st.subheader("💡 行权方式说明")
    st.markdown(f"> {exercise_method}：{EXERCISE_METHODS[exercise_method]['desc']}")

# 免责声明
st.divider()
st.markdown("> ⚠️ 免责声明：本工具为参考版，实际税款请以当地税务机关核定为准，建议咨询专业税务师。")
