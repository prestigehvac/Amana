import pdfplumber
import pandas as pd
import re
import json
import sqlite3
import os
import streamlit as st

st.set_page_config(page_title="Prestige HVAC Quote Helper", layout="wide")

def load_catalog_config(config_path="config.json"):
    """Loads layout rules from an external JSON file. 
    Falls back to defaults if the file doesn't exist."""
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)["Amana_Split_Catalog"]
    else:
        print(f"Warning: {config_path} not found. Using default fallback patterns.")
        return {
            "model_prefixes": ["AMVT", "AHVE", "AMST"],
            "heat_kit_prefixes": ["HKTSD", "HKTPD"],
            "voltage_patterns": ["208/230V", "115V"]
        }

def extract_amana_catalog_split(pdf_path):
    config = load_catalog_config()
    model_prefixes = tuple(config["model_prefixes"])
    heat_kit_prefixes = tuple(config["heat_kit_prefixes"])
    voltage_regex = r"(" + "|".join(config["voltage_patterns"]) + r")"
    
    furnace_data = []
    air_handler_data = []
    
    furnace_headers = [
        "Tonnage", "Condenser Model", "Condenser Price", "Condenser HxWxD",
        "Furnace Model", "Furnace Dimensions", "Furnace Price", 
        "Evap Coil", "Evap Coil Price", "SEER(2)", "EER(2)", "CCAP(2)", "AHRI", "Total"
    ]
    
    air_handler_headers = [
        "Tonnage", "Condenser/HP Model", "Base Unit Price", "Base Unit HxWxD",
        "Air Handler Model", "Air Handler HxWxD", "Voltage", "Air Handler Price",
        "Heat Kit", "Heat Kit Price", "SEER(2)", "EER(2)", "CCAP(2)", "AHRI", "Total"
    ]
    
    current_tonnage = None
    current_condenser = None
    current_condenser_price = None
    current_condenser_dim = None
    inside_air_handler_grid = True
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
                
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                
                if "Ton |" in line and "|" in line:
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 4:
                        current_tonnage = parts[0]
                        current_condenser = parts[1]
                        current_condenser_price = parts[2]
                        current_condenser_dim = parts[3]
                    inside_air_handler_grid = True
                    continue
                
                if "Handler for S-Series" in line or "Communi Air" in line or "Handler with" in line:
                    inside_air_handler_grid = False
                    continue
                    
                if "HxWxD" in line or "System Notes:" in line or "Page" in line or not line or not inside_air_handler_grid:
                    continue
                
                hk_pattern = "|".join(config["heat_kit_prefixes"])
                line = re.sub(rf'((?:{hk_pattern})[A-Z0-9]*)\$', r'\1 $', line)
                line = re.sub(r'(\d+)-\s*1/2', r'\1-1/2', line)
                line = re.sub(r'(\d+[\d\s/-]*)\"\s*x\s*([\d\s/-]+\")', r'\1"x\2', line)
                line = re.sub(r'\$\s+', '$', line)
                
                tokens = re.split(r'\s+', line)
                if len(tokens) >= 8:
                    prefix = tokens[0]
                    
                    if prefix.startswith(("ARVT", "AR9S")):
                        if len(tokens) >= 10 and tokens[2].startswith('$'):
                            furnace_model = tokens[0]
                            furnace_dim = tokens[1]
                            furnace_price = tokens[2]
                            evap_coil = tokens[3]
                            evap_price = tokens[4]
                            
                            total = tokens[-1]
                            ahri = tokens[-2]
                            ccap = tokens[-3]
                            eer = tokens[-4]
                            seer = tokens[-5]
                            
                            furnace_data.append([
                                current_tonnage, current_condenser, current_condenser_price, current_condenser_dim,
                                furnace_model, furnace_dim, furnace_price,
                                evap_coil, evap_price, seer, eer, ccap, ahri, total
                            ])
                    
                    elif prefix.startswith(model_prefixes):
                        air_handler_model = tokens[0]
                        
                        total = tokens[-1]
                        ahri = tokens[-2]
                        ccap = tokens[-3]
                        
                        if tokens[-5] == "-" or (re.match(r'^\d+\.\d+$', tokens[-5]) and re.match(r'^\d+\.\d+$', tokens[-6])):
                            eer = tokens[-5]
                            seer = tokens[-6]
                            meta_offset = -6
                        else:
                            eer = tokens[-4]
                            seer = tokens[-5]
                            meta_offset = -5
                        
                        equipment_tokens = tokens[:meta_offset]
                        equipment_part = " ".join(equipment_tokens)
                        
                        model_pattern = "|".join(config["model_prefixes"])
                        dim_match = re.search(rf'(?:{model_pattern})[A-Z0-9\-*]*\s+([\d\-xX/\"\']+)', line)
                        if dim_match:
                            raw_dim_start = dim_match.group(1)
                            full_dim_match = re.search(r'\b\d[\d\s\-/\"\'xX]*?\b(?=\s+(?:208/230V|115V|\$))', equipment_part)
                            air_handler_dim = full_dim_match.group(0).strip() if full_dim_match else raw_dim_start
                        else:
                            air_handler_dim = tokens[1] if (len(tokens) > 1 and "x" in tokens[1].lower()) else "-"
                        
                        volt_match = re.search(voltage_regex, equipment_part)
                        voltage = volt_match.group(0) if volt_match else "208/230V"
                        
                        ah_price = "-"
                        heat_kit = "-"
                        heat_kit_price = "-"
                        
                        price_index = 0
                        for i, token in enumerate(equipment_tokens):
                            if token.startswith('$'):
                                if price_index == 0:
                                    ah_price = token
                                    price_index += 1
                                elif price_index == 1:
                                    heat_kit_price = token
                                    price_index += 1
                            elif token.startswith(heat_kit_prefixes):
                                heat_kit = token
                                if i + 1 < len(equipment_tokens) and equipment_tokens[i+1].startswith('$'):
                                    heat_kit_price = equipment_tokens[i+1]
                                    price_index = 2
                        
                        if ah_price != "-" and heat_kit != "-" and heat_kit_price == "-":
                            heat_kit_price = ah_price
                            ah_price = "-"
                        
                        air_handler_data.append([
                            current_tonnage, current_condenser, current_condenser_price, current_condenser_dim,
                            air_handler_model, air_handler_dim, voltage, ah_price,
                            heat_kit, heat_kit_price, seer, eer, ccap, ahri, total
                        ])
                        
    df_furnace = pd.DataFrame(furnace_data, columns=furnace_headers)
    df_air_handler = pd.DataFrame(air_handler_data, columns=air_handler_headers)
    
    return df_furnace, df_air_handler


# --- 1. ADMIN SECTION FOR BACKEND PDF UPLOADS ---
st.sidebar.title("⚙️ Admin Controls")
uploaded_file = st.sidebar.file_uploader("Upload New Distributor Pricing PDF", type=["pdf"])

if uploaded_file is not None:
    if st.sidebar.button("🚀 Process & Update Catalog"):
        with st.spinner("Parsing new PDF data and updating database..."):
            temp_pdf_path = "temp_uploaded_catalog.pdf"
            with open(temp_pdf_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            try:
                df_gas, df_ah = extract_amana_catalog_split(temp_pdf_path)
                
                # Update spreadsheet in-memory storage if admin uploads a file
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                df_gas.to_sql("gas_furnaces", conn, if_exists="replace", index=False)
                df_ah.to_sql("air_handlers", conn, if_exists="replace", index=False)
                st.sidebar.success("Catalog updated successfully!")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Error processing PDF: {e}")
            finally:
                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)


# --- 2. TECHNICIAN INTERFACE ---
st.title("⚡ Prestige Quick Quote Tool")

# 1. Change the cache decorator to cache the dataframes instead of the connection
@st.cache_data
def load_excel_data():
    df_gas = pd.read_excel("Amana_Split_Pricing.xlsx", sheet_name="Gas Furnace Systems")
    df_ah = pd.read_excel("Amana_Split_Pricing.xlsx", sheet_name="Air Handler Systems")
    return df_gas, df_ah

# 2. Re-write your connection function to build a fresh database on every rerun
def get_database_connection():
    # Load the cached dataframes
    df_gas, df_ah = load_excel_data()
    
    # Establish a fresh, live in-memory database
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    
    # Dump the dataframes into the live connection
    df_gas.to_sql("gas_furnaces", conn, if_exists="replace", index=False)
    df_ah.to_sql("air_handlers", conn, if_exists="replace", index=False)
    return conn

# 3. Call your connection normally downstream
conn = get_database_connection()

# Flexible verification check
inspector = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", conn)
existing_tables = inspector['name'].tolist()

if not existing_tables:
    st.warning("⚠️ No catalog data found in the system database yet.")
else:
    system_type = st.selectbox("Select System Type", ["Air Handler Systems", "Gas Furnace Systems"])
    target_table = "air_handlers" if system_type == "Air Handler Systems" else "gas_furnaces"
    condenser_col = "Condenser/HP Model" if system_type == "Air Handler Systems" else "Condenser Model"

    tonnages = pd.read_sql(f"SELECT DISTINCT Tonnage FROM {target_table}", conn)["Tonnage"].tolist()
    selected_ton = st.selectbox("Select Tonnage", tonnages)
    
    condensers = pd.read_sql(f"SELECT DISTINCT [{condenser_col}] FROM {target_table} WHERE Tonnage='{selected_ton}'", conn)[condenser_col].tolist()
    selected_condenser = st.selectbox("Select Condenser Model", condensers)
    
    if system_type == "Air Handler Systems":
        query = f"SELECT [Air Handler Model], [Air Handler HxWxD], [Air Handler Price], [Heat Kit], [Heat Kit Price], [SEER(2)], [Total] FROM air_handlers WHERE [Condenser/HP Model]='{selected_condenser}'"
    else:
        query = f"SELECT [Furnace Model], [Furnace Dimensions], [Furnace Price], [Evap Coil], [Evap Coil Price], [SEER(2)], [Total] FROM gas_furnaces WHERE [Condenser Model]='{selected_condenser}'"
        
    results = pd.read_sql(query, conn)
    
    st.sidebar.header("Pricing Calculator")
    markup_multiplier = st.sidebar.slider("Markup Multiplier", 1.0, 3.0, 1.5, step=0.1)
    flat_labor = st.sidebar.number_input("Flat Labor Cost ($)", value=1200)

    if not results.empty:
        raw_totals = results["Total"].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).astype(float)
        results["Retail Equipment Price"] = raw_totals * markup_multiplier
        results["Total Customer Investment"] = results["Retail Equipment Price"] + flat_labor
        
        results["Retail Equipment Price"] = results["Retail Equipment Price"].map('${:,.2f}'.format)
        results["Total Customer Investment"] = results["Total Customer Investment"].map('${:,.2f}'.format)

    st.subheader("Available Matchups & Customer Pricing")
    st.dataframe(results, use_container_width=True)

conn.close()
# sync refresh

st.subheader("Available Matchups & Customer Pricing")
st.dataframe(results, use_container_width=True)

conn.close()
