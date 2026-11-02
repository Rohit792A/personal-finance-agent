# app/frontend.py
import streamlit as st
import pandas as pd
import sqlalchemy

from database import DatabaseManager

# 1. Page Configuration
st.set_page_config(page_title="Finance AI", page_icon="💰", layout="wide")
st.title("💰 AI Personal Finance Dashboard")


# 2. Initialize Database Connection (Cached so it doesn't reconnect on every click)
@st.cache_resource
def get_db_manager():
    return DatabaseManager()


db_manager = get_db_manager()


# 3. Fetch Data Functions
def load_transactions():
    with db_manager.pool.connect() as conn:
        # We join the categories table so you see "Food" instead of ID "1"
        query = sqlalchemy.text("""
            SELECT t.id, t.transaction_date as "Date", t.amount as "Amount", t.description as "Description", c.name as "Category", t.transaction_type as "Type", t.ai_confidence_score as "AI Confidence"
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            ORDER BY t.transaction_date DESC
        """)
        return pd.read_sql(query, conn)


def load_summary():
    with db_manager.pool.connect() as conn:
        # Fetch from the View we created in Step 31
        query = sqlalchemy.text("SELECT * FROM financial_summary")
        return pd.read_sql(query, conn)


# 4. Render the UI
tab1, tab2 = st.tabs(["💰 All Transactions", "📊 Monthly Summary"])

with tab1:
    st.subheader("Your Transactions")
    st.markdown("View and interact with your ingested data below.")
    try:
        df_transactions = load_transactions()
        # st.data_editor renders an interactive Excel-like grid!
        edited_df = st.data_editor(
            df_transactions,
            use_container_width=True, hide_index=True,
            disabled=["id", "AI Confidence"]  # Prevent editing the ID or AI score
        )
    except Exception as e:
        st.error(f"Could not load transactions: {e}")

with tab2:
    st.subheader("Financial Summary by Category")
    try:
        df_summary = load_summary()
        st.dataframe(df_summary, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Could not load summary: {e}")