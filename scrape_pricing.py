import pdfplumber
import pandas as pd
import re
import json
import os

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
    # Load configuration parameters dynamically
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
    
    print("Parsing catalog pages... Please wait.")
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
                
                # Pre-clean spacing boundaries using prefixes loaded from config
                hk_pattern = "|".join(config["heat_kit_prefixes"])
                line = re.sub(rf'((?:{hk_pattern})[A-Z0-9]*)\$', r'\1 $', line)
                line = re.sub(r'(\d+)-\s*1/2', r'\1-1/2', line)
                line = re.sub(r'(\d+[\d\s/-]*)\"\s*x\s*([\d\s/-]+\")', r'\1"x\2', line)
                line = re.sub(r'\$\s+', '$', line)
                
                tokens = re.split(r'\s+', line)
                if len(tokens) >= 8:
                    prefix = tokens[0]
                    
                    # --- GAS FURNACE LOGIC ---
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
                    
                    # --- AIR HANDLER LOGIC ---
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
                        
                        # Dimension tracking
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

if __name__ == "__main__":
    pdf_name = "Amana.pdf"
    df_gas, df_ah = extract_amana_catalog_split(pdf_path=pdf_name)

    output_excel = "Amana_Split_Pricing.xlsx"
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        df_gas.to_excel(writer, sheet_name="Gas Furnace Systems", index=False)
        df_ah.to_excel(writer, sheet_name="Air Handler Systems", index=False)
    print("Success! Executed parsing engine with externalized JSON parameters.")