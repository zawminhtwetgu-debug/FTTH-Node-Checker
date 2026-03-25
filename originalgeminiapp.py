import math, folium, requests, time, io
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(layout="wide", page_title="FTTH Smart Node Checker")

MAX_DISTANCE = 350
MAX_CAPACITY = 16 
TOP_FALLBACK_NODES = 5 

# =========================================================
# SESSION STATE
# =========================================================
for key in ["batch_done", "batch_summary_df", "batch_results", "single_res"]:
    if key not in st.session_state: st.session_state[key] = None if "df" in key or "res" in key else False

# =========================================================
# UTILS
# =========================================================
def clean_num(value):
    try:
        val = pd.to_numeric(str(value).replace("°", "").replace(",", "").strip(), errors="coerce")
        return val
    except: return None

@st.cache_data(ttl=3600)
def get_route_osrm(lat1, lon1, lat2, lon2):
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2): return (None, None)
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
    try:
        r = requests.get(url, timeout=10).json()
        return (r['routes'][0]['distance'], r['routes'][0]['geometry']) if r['code'] == 'Ok' else (None, None)
    except: return (None, None)

def haversine(lat1, lon1, lat2, lon2):
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2): return 999999
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1-a)))

# =========================================================
# CORE LOGIC (350m & 16 Port Fix)
# =========================================================
def analyze_one_customer(nodes_df, cust_name, cust_lat, cust_lon, connected_name):
    res = {
        "customer_name": cust_name, "cust_lat": cust_lat, "cust_lon": cust_lon, 
        "connected_name": connected_name or "-", "connected_status": "NOK", 
        "connected_reason": "-", "connected_dist": "-", 
        "connected_map_obj": None, "recommended_map_obj": None, "final_status": "NOK"
    }
    
    if pd.isna(cust_lat) or pd.isna(cust_lon):
        res["connected_reason"] = "Invalid Lat/Long"
        return res

    # 1. Connected Node Logic
    if connected_name and connected_name != "-":
        node = nodes_df[nodes_df["node_name"].str.strip() == str(connected_name).strip()]
        if not node.empty:
            n = node.iloc[0]
            d, g = get_route_osrm(cust_lat, cust_lon, n["Latitude"], n["Longitude"])
            
            # Distance check (350m) & Capacity check (16 customers)
            is_dist_ok = (d is not None and d <= MAX_DISTANCE)
            is_port_ok = (n["act"] < MAX_CAPACITY)
            
            if is_dist_ok and is_port_ok:
                stat, reas = "Can Deploy", "OK"
            elif not is_dist_ok:
                stat, reas = "NOK", "Over Meter"
            else:
                stat, reas = "NOK", "Full Port"
                
            res.update({"connected_status": stat, "connected_reason": reas, "connected_dist": int(d) if d else "-"})
            res["connected_map_obj"] = {"name": n["node_name"], "lat": n["Latitude"], "lon": n["Longitude"], "dist": d, "geom": g, "status": stat, "reason": reas}

    # 2. Recommended Node (Only if connected isn't "Can Deploy")
    if res["connected_status"] != "Can Deploy":
        temp = nodes_df.copy()
        temp["straight"] = temp.apply(lambda r: haversine(cust_lat, cust_lon, r["Latitude"], r["Longitude"]), axis=1)
        # Find best alternative within 350m & < 16 customers
        for _, n in temp.sort_values("straight").head(TOP_FALLBACK_NODES).iterrows():
            if n["node_name"] == connected_name: continue
            d, g = get_route_osrm(cust_lat, cust_lon, n["Latitude"], n["Longitude"])
            if d and d <= MAX_DISTANCE and n["act"] < MAX_CAPACITY:
                res["recommended_map_obj"] = {"name": n["node_name"], "lat": n["Latitude"], "lon": n["Longitude"], "dist": d, "geom": g, "status": "Can Deploy"}
                break

    res["final_status"] = "Can Deploy" if (res["connected_status"] == "Can Deploy" or res["recommended_map_obj"]) else "NOK"
    return res

def draw_map(cust_name, cust_lat, cust_lon, conn=None, reco=None):
    if pd.isna(cust_lat) or pd.isna(cust_lon): return folium.Map(location=[16.8, 96.1], zoom_start=12)
    m = folium.Map(location=[cust_lat, cust_lon], zoom_start=17)
    folium.Marker([cust_lat, cust_lon], tooltip=f"Customer: {cust_name}", icon=folium.Icon(color="blue")).add_to(m)
    for obj, color in [(conn, "red"), (reco, "green")]:
        if obj and obj.get("geom"):
            folium.GeoJson(obj["geom"], style_function=lambda x, c=color: {"color": c, "weight": 5}).add_to(m)
            folium.Marker([obj["lat"], obj["lon"]], tooltip=obj["name"], icon=folium.Icon(color=color)).add_to(m)
    return m

# =========================================================
# UI
# =========================================================
st.title("FTTH Smart Node Checker")
st.markdown("##### Powered by Zaw Min Htwe")
st.info("Survey Result အား Routing အကွာအဝေးကို လမ်းကြောင်းအတိုင်း တွက်ချက်ပေးပါသည်။")

t1, t2 = st.tabs(["Batch Check", "Single Check"])

with t1:
    f_n, f_c, f_x = st.file_uploader("Nodes (CSV)", key="n1"), st.file_uploader("Customers (CSV)", key="c1"), st.file_uploader("New Customers (XLSX)", key="x1")
    
    col_b1, col_b2 = st.columns(2)
    if col_b1.button("Run Batch", type="primary"):
        if f_n and f_c and f_x:
            nodes = pd.read_csv(f_n).merge(pd.read_csv(f_c).groupby("node_name").size().reset_index(name="act"), on="node_name", how="left")
            nodes["act"] = nodes["act"].fillna(0)
            new_custs = pd.read_excel(f_x).dropna(subset=['customer_name'])
            results, summary_rows = [], []
            prog = st.progress(0)
            for i, (_, r) in enumerate(new_custs.iterrows()):
                res = analyze_one_customer(nodes, r["customer_name"], clean_num(r["lat"]), clean_num(r["Long"]), r.get("connected_node"))
                results.append(res)
                summary_rows.append({
                    "Customer Name": res["customer_name"],
                    "Status": res["connected_status"],
                    "Reason": res["connected_reason"],
                    "Connected Node": res["connected_name"],
                    "Distance (m)": res["connected_dist"],
                    "Recommended": res["recommended_map_obj"]["name"] if res["recommended_map_obj"] else "-"
                })
                prog.progress((i+1)/len(new_custs))
            st.session_state.batch_results, st.session_state.batch_summary_df, st.session_state.batch_done = results, pd.DataFrame(summary_rows), True

    if col_b2.button("Clear Batch"):
        st.session_state.batch_done = False; st.rerun()

    if st.session_state.batch_done:
        st.dataframe(st.session_state.batch_summary_df, use_container_width=True)
        # Excel Column width auto-fix
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            st.session_state.batch_summary_df.to_excel(writer, index=False, sheet_name='Sheet1')
            worksheet = writer.sheets['Sheet1']
            for i, col in enumerate(st.session_state.batch_summary_df.columns):
                w = max(st.session_state.batch_summary_df[col].astype(str).str.len().max(), len(col)) + 2
                worksheet.set_column(i, i, min(w, 50))
        st.download_button("Download Excel Report", data=output.getvalue(), file_name="Report.xlsx")
        
        st.divider()
        sel = st.selectbox("Select Customer to View Detail", [r["customer_name"] for r in st.session_state.batch_results])
        res = next(r for r in st.session_state.batch_results if r["customer_name"] == sel)
        
        st.markdown(f"### Customer Survey Result\n---")
        st.write(f"**Customer:** {res['customer_name']}")
        st.write(f"**Connected Node:** {res['connected_name']}")
        st.write(f"**Status:** {res['connected_status']}")
        st.write(f"**Reason:** {res['connected_reason']}")
        dist_label = "Over Meter" if res['connected_reason'] == "Over Meter" else "Distance"
        st.write(f"**{dist_label}:** {res['connected_dist']}m")
        st_folium(draw_map(res["customer_name"], res["cust_lat"], res["cust_lon"], res["connected_map_obj"], res["recommended_map_obj"]), height=500, width=1000, key=f"m_{sel}")

with t2:
    st.subheader("Single Customer Check")
    f_sn, f_sc = st.file_uploader("Nodes (CSV)", key="n2"), st.file_uploader("Customers (CSV)", key="c2")
    sl1, sl2 = st.columns(2)
    with sl1: s_na, s_la = st.text_input("Customer Name"), st.text_input("Latitude")
    with sl2: s_no, s_lo = st.text_input("Connected Node"), st.text_input("Longitude")
    
    col_s1, col_s2 = st.columns(2)
    if col_s1.button("Run Single"):
        if f_sn and f_sc and s_la and s_lo:
            # Fix: Ensure nodes data with port counts is loaded exactly same as Batch Check
            nodes_s = pd.read_csv(f_sn).merge(pd.read_csv(f_sc).groupby("node_name").size().reset_index(name="act"), on="node_name", how="left")
            nodes_s["act"] = nodes_s["act"].fillna(0)
            st.session_state.single_res = analyze_one_customer(nodes_s, s_na, clean_num(s_la), clean_num(s_lo), s_no)
    
    if col_s2.button("Clear Single"):
        st.session_state.single_res = None; st.rerun()

    if st.session_state.single_res:
        r = st.session_state.single_res
        st.markdown(f"### Customer Survey Result\n---")
        st.write(f"**Customer:** {r['customer_name']}")
        st.write(f"**Connected Node:** {r['connected_name']}")
        st.write(f"**Status:** {r['connected_status']}")
        st.write(f"**Reason:** {r['connected_reason']}")
        dist_label = "Over Meter" if r['connected_reason'] == "Over Meter" else "Distance"
        st.write(f"**{dist_label}:** {r['connected_dist']}m")
        st_folium(draw_map(r["customer_name"], r["cust_lat"], r["cust_lon"], r["connected_map_obj"], r["recommended_map_obj"]), height=500, width=1000, key="ms")