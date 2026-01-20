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
# 1. 激励工具规则
INCENTIVE_TOOLS = {
    "期权": {
        "income_formula": "行权收入 =（行权日市价 - 行权价）× 行权数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    },
    "限制性股票": {
        "income_formula": "行权收入 = 解禁日市价 × 解禁数量",
        "income_calc": lambda ep, mp, q, *args: mp * q
    },
    "股票增值权": {
        "income_formula": "行权收入 =（行权日市价 - 授予价）× 行权数量",
        "income_calc": lambda ep, mp, q, *args: (mp - ep) * q
    }
}

# 2. 行权方式规则
EXERCISE_METHODS = {
    "现金行权": {
        "desc": "现金支付行权价，全额持有股票",
        "actual_quantity": lambda q, tax, ep, mp: q,
        "formula": "实际持有数量=行权数量"
    },
    "卖股缴税": {
        "desc": "卖出部分股票支付税款，剩余持有",
        "actual_quantity": lambda q, tax, ep, mp: q - math.ceil(tax / (mp or 1)),
        "formula": "实际持有数量=行权数量 - 向上取整(税款÷市价)"
    },
    "无现金行权": {
        "desc": "券商垫付行权价，卖出部分股票偿还",
        "actual_quantity": lambda q, tax, ep, mp: q - math.ceil((ep*q + tax) / (mp or 1)),
        "formula": "实际持有数量=行权数量 - 向上取整((行权总价+税款)÷市价)"
    }
}

# 3. 税务规则
TAX_RULES = {
    "中国大陆": {
        "annual_brackets": [
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
        "policy_basis": "财政部 税务总局公告2023年第25号"
    },
    "中国香港": {
        "annual_brackets": [
            (50000, 0.02, 0), (50000, 0.06, 1000), (50000, 0.1, 3000),
            (50000, 0.14, 5000), (float('inf'), 0.17, 7000)
        ],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True
    },
    "新加坡": {
        "annual_brackets": [
            (20000, 0.02, 0), (10000, 0.035, 400), (10000, 0.07, 750),
            (40000, 0.115, 1150), (40000, 0.15, 2750), (40000, 0.18, 4750),
            (40000, 0.19, 6550), (40000, 0.2, 8150), (float('inf'), 0.22, 8950)
        ],
        "transfer_tax_rate": 0.0,
        "transfer_tax_exempt": True
    }
}

# ---------------------- 税率计算函数 ----------------------
def calculate_single_tax(income, brackets):
    income = max(income, 0.0)
    for upper, rate, deduction in brackets:
        if income <= upper:
            return round(income * rate - deduction, 2)
    upper, rate, deduction = brackets[-1]
    return round(income * rate - deduction, 2)

# ---------------------- 核心计算函数 ----------------------
def calculate_single_record(record, tax_resident, is_listed, listing_location):
    record_id = record["id"]
    incentive_tool = record["incentive_tool"]
    exercise_method = record["exercise_method"]
    ep = record["exercise_price"]
    eq = record["exercise_quantity"]
    mp = record["exercise_market_price"]
    tp = record["transfer_price"]

    exercise_income = INCENTIVE_TOOLS[incentive_tool]["income_calc"](ep, mp, eq)
    exercise_income = max(exercise_income, 0.0)

    rule = TAX_RULES[tax_resident]
    single_tax = calculate_single_tax(exercise_income, rule["annual_brackets"])

    actual_qty = EXERCISE_METHODS[exercise_method]["actual_quantity"](eq, single_tax, ep, mp)
    actual_qty = max(actual_qty, 0)

    tax_shares = 0
    remaining_shares = 0
    if exercise_method == "卖股缴税":
        tax_shares = math.ceil(single_tax / (mp or 1))
        tax_shares = max(tax_shares, 0)
        remaining_shares = eq - tax_shares
        remaining_shares = max(remaining_shares, 0)
    else:
        tax_shares = "——"
        remaining_shares = "——"

    transfer_income = 0.0
    transfer_tax = 0.0
    if tp > 0 and actual_qty > 0:
        transfer_income = (tp - mp) * actual_qty
        transfer_income = max(transfer_income, 0.0)
        if not (rule["transfer_tax_exempt"] and listing_location == "境内"):
            transfer_tax = round(transfer_income * rule["transfer_tax_rate"], 2)

    return {
        "记录ID": record_id,
        "激励工具类型": incentive_tool,
        "行权方式": exercise_method,
        "行权价(元/股)": ep,
        "行权数量(股)": eq,
        "行权日市价(元/股)": mp,
        "转让价(元/股)": tp,
        "行权收入(元)": exercise_income,
        "应缴税款(元)": single_tax,
        "抵税股出售数量(股)": tax_shares,
        "剩余到账股数(股)": remaining_shares,
        "实际持有数量(股)": actual_qty,
        "转让收入(元)": transfer_income,
        "转让税款(元)": transfer_tax
    }

def calculate_yearly_consolidation(detail_records, tax_resident, is_listed, listing_location, other_income, special_deduction):
    rule = TAX_RULES[tax_resident]
    total_exercise_income = sum([r["行权收入(元)"] for r in detail_records])
    total_transfer_income = sum([r["转让收入(元)"] for r in detail_records])
    total_transfer_tax = sum([r["转让税款(元)"] for r in detail_records])
    total_exercise_tax = 0.0

    if tax_resident == "中国大陆":
        if is_listed:
            total_exercise_tax = calculate_single_tax(total_exercise_income, rule["annual_brackets"])
            tax_desc = "上市公司股权激励单独计税"
        else:
            taxable_income = max(total_exercise_income + other_income - 60000 - special_deduction, 0.0)
            total_exercise_tax = calculate_single_tax(taxable_income, rule["annual_brackets"])
            tax_desc = "非上市公司股权激励并入综合所得计税"
    else:
        total_exercise_tax = calculate_single_tax(total_exercise_income, rule["annual_brackets"])
        tax_desc = f"{tax_resident} 当地规则计税"

    total_yearly_tax = round(total_exercise_tax + total_transfer_tax, 2)
    total_yearly_income = round(total_exercise_income + total_transfer_income, 2)
    net_income = round(total_yearly_income - total_yearly_tax, 2)

    return {
        "税务居民身份": tax_resident,
        "是否上市公司": "是" if is_listed else "否",
        "上市地": listing_location,
        "年度股权激励总收入(元)": total_exercise_income,
        "年度股权激励税款(元)": total_exercise_tax,
        "年度转让收入(元)": total_transfer_income,
        "年度转让税款(元)": total_transfer_tax,
        "年度总税款(元)": total_yearly_tax,
        "年度总收益(元)": total_yearly_income,
        "年度净收益(元)": net_income,
        "计税规则说明": tax_desc
    }

# ---------------------- 报税表单生成函数 ----------------------
def generate_tax_form(yearly_result, detail_records, tax_resident):
    rule = TAX_RULES[tax_resident]
    form_data_list = []
    for r in detail_records:
        form_data = {
            "记录ID": r["记录ID"],
            "激励工具类型": r["激励工具类型"],
            "行权方式": r["行权方式"],
            "行权收入(元)": r["行权收入(元)"],
            "应缴税款(元)": r["应缴税款(元)"],
            "转让收入(元)": r["转让收入(元)"],
            "转让税款(元)": r["转让税款(元)"]
        }
        if tax_resident == "中国大陆":
            form_data["应纳税所得额"] = yearly_result["年度股权激励总收入(元)"]
            form_data["适用税率"] = "3%-45%（单独计税）" if yearly_result["是否上市公司"] == "是" else "3%-45%（并入综合所得）"
            form_data["最终应缴税额"] = yearly_result["年度股权激励税款(元)"]
        else:
            form_data["应纳税所得额"] = r["行权收入(元)"]
            form_data["适用税率"] = f"{rule['annual_brackets'][-1][1] * 100}%"
            form_data["最终应缴税额"] = r["应缴税款(元)"]
        form_data_list.append(form_data)
    
    summary_form_data = {
        "记录ID": "年度汇总",
        "激励工具类型": "合并计算",
        "行权方式": "——",
        "行权收入(元)": yearly_result["年度股权激励总收入(元)"],
        "应缴税款(元)": yearly_result["年度股权激励税款(元)"],
        "转让收入(元)": yearly_result["年度转让收入(元)"],
        "转让税款(元)": yearly_result["年度转让税款(元)"],
        "应纳税所得额": yearly_result["年度股权激励总收入(元)"],
        "适用税率": form_data["适用税率"],
        "最终应缴税额": yearly_result["年度总税款(元)"]
    }
    form_data_list.append(summary_form_data)
    return pd.DataFrame(form_data_list)

# ---------------------- 结果导出函数 ----------------------
def export_to_excel(detail_records, yearly_result, tax_form_df):
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine="xlsxwriter")
    pd.DataFrame(detail_records).to_excel(writer, sheet_name="交易明细", index=False)
    pd.DataFrame([yearly_result]).to_excel(writer, sheet_name="年度汇总", index=False)
    tax_form_df.to_excel(writer, sheet_name="报税表单", index=False)
    writer.close()
    output.seek(0)
    return output

# ---------------------- 页面主体 ----------------------
st.title("股权激励计税工具")
st.caption(TAX_RULES["中国大陆"]["policy_basis"])
st.divider()

# ---------------------- 1. 全局参数初始化 ----------------------
if "tax_resident" not in st.session_state:
    st.session_state.tax_resident = "中国大陆"
if "is_listed" not in st.session_state:
    st.session_state.is_listed = True
if "listing_location" not in st.session_state:
    st.session_state.listing_location = "境内"
if "equity_records" not in st.session_state:
    st.session_state.equity_records = [
        {
            "id": 1,
            "incentive_tool": "期权",
            "exercise_method": "卖股缴税",
            "exercise_price": 120.0,
            "exercise_quantity": 1800,
            "exercise_market_price": 240.0,
            "transfer_price": 0.0
        }
    ]

# ---------------------- 2. 侧边栏设置 ----------------------
with st.sidebar:
    st.header("参数设置")
    st.session_state.tax_resident = st.selectbox("税务居民身份", list(TAX_RULES.keys()))
    st.session_state.is_listed = st.checkbox("是否上市公司", value=True)
    st.session_state.listing_location = st.selectbox("上市地", ["境内", "境外"])

    other_income = 0.0
    special_deduction = 0.0
    if st.session_state.tax_resident == "中国大陆" and not st.session_state.is_listed:
        st.subheader("综合所得扣除项")
        other_income = st.number_input("年度其他综合所得", min_value=0.0, step=1000.0, value=0.0)
        special_deduction = st.number_input("年度专项附加扣除", min_value=0.0, step=1000.0, value=0.0)

    st.divider()
    st.subheader("记录操作")
    col_add, col_del = st.columns(2)
    with col_add:
        if st.button("添加记录"):
            new_id = len(st.session_state.equity_records) + 1
            st.session_state.equity_records.append({
                "id": new_id,
                "incentive_tool": "期权",
                "exercise_method": "卖股缴税",
                "exercise_price": 120.0,
                "exercise_quantity": 1800,
                "exercise_market_price": 240.0,
                "transfer_price": 0.0
            })
    with col_del:
        if st.button("删除最后一条"):
            if len(st.session_state.equity_records) > 1:
                st.session_state.equity_records.pop()

    if st.button("重置参数"):
        st.session_state.clear()
        st.rerun()

    calc_btn = st.button("计算", use_container_width=True)

# ---------------------- 3. 交易记录输入 ----------------------
st.subheader("交易记录")
for idx, record in enumerate(st.session_state.equity_records):
    with st.expander(f"记录 {record['id']}", expanded=True):
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
            price_label = "行权价(元/股)" if record["incentive_tool"] != "限制性股票" else "授予价(元/股)"
            record["exercise_price"] = st.number_input(price_label, min_value=0.0, step=1.0, value=record["exercise_price"], key=f"price_{record['id']}")
            record["exercise_quantity"] = st.number_input("行权数量(股)", min_value=1, step=100, value=record["exercise_quantity"], key=f"qty_{record['id']}")
        with col4:
            record["exercise_market_price"] = st.number_input("行权日市价(元/股)", min_value=0.0, step=1.0, value=record["exercise_market_price"], key=f"mp_{record['id']}")
            record["transfer_price"] = st.number_input("转让价(元/股)", min_value=0.0, step=1.0, value=record["transfer_price"], key=f"tp_{record['id']}")
st.divider()

# ---------------------- 4. 计算与结果展示 ----------------------
if calc_btn:
    valid_records = [r for r in st.session_state.equity_records if r["exercise_quantity"] > 0]
    if not valid_records:
        st.error("无有效交易记录")
    else:
        detail_results = [calculate_single_record(
            r, st.session_state.tax_resident, st.session_state.is_listed, st.session_state.listing_location
        ) for r in valid_records]
        yearly_result = calculate_yearly_consolidation(
            detail_results, st.session_state.tax_resident, st.session_state.is_listed,
            st.session_state.listing_location, other_income, special_deduction
        )
        tax_form_df = generate_tax_form(yearly_result, detail_results, st.session_state.tax_resident)

        # ---------------------- 关键指标仪表盘 ----------------------
        st.subheader("关键指标")
        
        # 总出售股数
        total_sold_shares = 0
        for res in detail_results:
            tax_shares = res["抵税股出售数量(股)"]
            if isinstance(tax_shares, int):
                total_sold_shares += tax_shares

        col_sold = st.columns(1)[0]
        with col_sold:
            st.metric(
                label="年内总抵税股出售数量",
                value=f"{total_sold_shares} 股"
            )
        
        # 财务指标
        st.markdown("")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                label="年度股权激励总收入",
                value=f"¥ {yearly_result['年度股权激励总收入(元)']:,.2f}"
            )
        with col2:
            st.metric(
                label="年度总税款",
                value=f"¥ {yearly_result['年度总税款(元)']:,.2f}"
            )
        with col3:
            st.metric(
                label="年度净收益",
                value=f"¥ {yearly_result['年度净收益(元)']:,.2f}"
            )
        st.divider()

        # 交易明细
        st.subheader("交易明细")
        show_cols = [
            "记录ID", "激励工具类型", "行权方式", "行权价(元/股)", 
            "行权数量(股)", "行权日市价(元/股)", "行权收入(元)", 
            "应缴税款(元)", "抵税股出售数量(股)", "剩余到账股数(股)", "实际持有数量(股)"
        ]
        st.dataframe(pd.DataFrame(detail_results)[show_cols], use_container_width=True)

        # 年度汇总
        st.subheader("年度汇总")
        st.dataframe(pd.DataFrame([yearly_result]), use_container_width=True)

        # 税款构成
        st.subheader("税款构成")
        tax_data = pd.DataFrame({
            "税款类型": ["股权激励税款", "转让税款"],
            "金额(元)": [yearly_result["年度股权激励税款(元)"], yearly_result["年度转让税款(元)"]]
        })
        fig = px.pie(tax_data, values="金额(元)", names="税款类型", hole=0.4)
        fig.update_layout(showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5))
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

        # 报税表单
        st.subheader("报税表单")
        st.dataframe(tax_form_df, use_container_width=True)

        # 导出
        st.subheader("导出")
        excel_data = export_to_excel(detail_records, yearly_result, tax_form_df)
        st.download_button(
            label="导出Excel文件",
            data=excel_data,
            file_name="股权激励计税结果.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# ---------------------- 免责声明 ----------------------
st.divider()
st.caption("本工具仅供参考，实际计税请以税务机关核定为准")
