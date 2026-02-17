import streamlit as st
import pandas as pd
import requests 
import datetime
import calendar
import folium # 🌟 지도 그리는 붓
from streamlit_folium import st_folium # 🌟 그린 지도를 웹에 띄워주는 액자
from database import STUDIO_DB

# 1. 페이지 설정
st.set_page_config(page_title="합주실 맵스캐너", page_icon="🎸", layout="wide")


# ⏱️ 시간 변환 함수
def convert_to_24h_set(time_string):
    if not time_string or str(time_string) == 'nan' or time_string == "-": return set()
    times = [t.strip() for t in time_string.split(',')]
    result_set = set()
    for t in times:
        current_period = "오후" if "오후" in t else "오전"
        try: 
            hour_str = t.replace("오전", "").replace("오후", "").replace("시", "").split(":")[0].strip()
            hour = int(hour_str)
            if current_period == "오전":
                result_set.add(0 if hour == 12 else hour)
            else:
                result_set.add(12 if hour == 12 else hour + 12)
        except: continue
    return result_set

def format_time_text(hour):
    if hour == 0: return "오전 12시"
    elif hour < 12: return f"오전 {hour}시"
    elif hour == 12: return "오후 12시"
    else: return f"오후 {hour - 12}시"

# ⚡️ 초광속 범용 API 스캐너 (달력 UI 호환 패치)
@st.cache_data(show_spinner=False)
def run_api_crawler(target_date_obj, selected_studios): # 변수명 변경!
    # 🌟 이제 스트림릿 달력에서 고른 '진짜 날짜 객체'가 바로 들어옴
    year = target_date_obj.year
    month = target_date_obj.month
    
    # 선택한 날짜가 포함된 달의 첫날과 마지막 날을 계산 (다음 달을 골라도 완벽 작동!)
    last_day = calendar.monthrange(year, month)[1]
    start_time_str = f"{year}-{month:02d}-01T00:00:00"
    end_time_str = f"{year}-{month:02d}-{last_day}T23:59:59"

    GRAPHQL_QUERY = """query hourlySchedule($scheduleParams: ScheduleParams) {\n  schedule(input: $scheduleParams) {\n    bizItemSchedule {\n      hourly {\n        unitStartDateTime\n        unitStartTime\n        unitBookingCount\n        unitStock\n        stock\n        bookingCount\n        isUnitSaleDay\n        isUnitBusinessDay\n      }\n    }\n  }\n}"""

    # ... (이 아래 for문부터는 기존 코드와 완전히 동일!) ...
    final_data = []
    for studio_name in selected_studios:
        if studio_name not in STUDIO_DB: continue
        for room in STUDIO_DB[studio_name]:
            url = room["url"]
            try:
                biz_id = url.split("bizes/")[1].split("/")[0]
                item_id = url.split("items/")[1].split("?")[0]
            except: continue

            headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Referer": url}
            payload = {
                "operationName": "hourlySchedule",
                "variables": {
                    "scheduleParams": {
                        "businessTypeId": 10, "businessId": biz_id, "bizItemId": item_id,
                        "startDateTime": start_time_str, "endDateTime": end_time_str,
                        "fixedTime": True, "includesHolidaySchedules": True
                    }
                },
                "query": GRAPHQL_QUERY
            }

            try:
                response = requests.post("https://m.booking.naver.com/graphql", json=payload, headers=headers)
                # 🌟 여기에 위치(lat, lon) 정보도 같이 저장해 둬야 나중에 지도에 찍을 수 있어!
                result = {"합주실 이름": room['name'], "상태": "❌ 마감", "예약 가능 시간": "-", "예약링크": url, "lat": room['lat'], "lon": room['lon']}
                
                if response.status_code == 200:
                    data = response.json()
                    hourly_slots = data.get('data', {}).get('schedule', {}).get('bizItemSchedule', {}).get('hourly', [])
                    available_times = []
                    
                    for slot in hourly_slots:
                        start_time_str_raw = slot.get('unitStartTime', '')
                        if not start_time_str_raw: continue
                        
                        try:
                            date_part = start_time_str_raw.split()[0] 
                            year, month, day = map(int, date_part.split('-'))
                            slot_date_obj = datetime.date(year, month, day)
                            
                            if slot_date_obj != target_date_obj: continue
                            
                            stock = int(slot.get('unitStock', slot.get('stock', 0)))
                            booked = int(slot.get('unitBookingCount', slot.get('bookingCount', 0)))
                            is_sale = slot.get('isUnitSaleDay', True) and slot.get('isUnitBusinessDay', True)
                            
                            if stock > booked and is_sale:
                                time_part = start_time_str_raw.split()[-1]
                                hour = int(time_part.split(":")[0])
                                available_times.append(format_time_text(hour))
                        except: continue
                        
                    if available_times:
                        available_times = sorted(list(set(available_times)), key=lambda x: int(convert_to_24h_set(x).copy().pop()) if convert_to_24h_set(x) else 0)
                        result["상태"] = "✅ 예약 가능"
                        result["예약 가능 시간"] = ", ".join(available_times)
                
                final_data.append(result)
            except: 
                result["상태"] = "⚠️ 탐색 실패"
                final_data.append(result)
            
    return pd.DataFrame(final_data)

# ==========================================
# ⏱️ [신규 추가] 연속 시간 판별 알고리즘
# ==========================================
def check_consecutive_hours(times_set, min_hours):
    if not times_set: return False
    sorted_times = sorted(list(times_set))
    max_len = 1
    current_len = 1
    for i in range(1, len(sorted_times)):
        if sorted_times[i] == sorted_times[i-1] + 1:
            current_len += 1
        else:
            max_len = max(max_len, current_len)
            current_len = 1
    max_len = max(max_len, current_len)
    return max_len >= min_hours
# ==========================================
# ⏱️ [신규 추가] 연속된 시간을 "OO시~OO시"로 예쁘게 묶어주는 함수
# ==========================================
def format_time_ranges(times_set):
    if not times_set: return "-"
    sorted_times = sorted(list(times_set))
    
    ranges = []
    start = sorted_times[0]
    prev = sorted_times[0]
    
    for t in sorted_times[1:]:
        if t == prev + 1: # 시간이 연속으로 이어지면
            prev = t      # 꼬리를 잡고 계속 늘림
        else:             # 시간이 끊기면
            ranges.append(f"{start}시~{prev+1}시") # 이전 블록을 저장
            start = t
            prev = t
            
    # 마지막 남은 블록 저장 (끝나는 시간은 +1시간 해줌)
    ranges.append(f"{start}시~{prev+1}시")
    return ", ".join(ranges)
# ==========================================
# 🎨 메인 대시보드 UI (화면 분할 & 아코디언 패치)
# ==========================================
st.title("🎸 [잼투게더] : 서울 합주실 스캐너")
st.write("1초만에 합주실 예약하기 🚀")

# 🌟 1. 팝업 대신 화면을 가리지 않는 아코디언(Expander) 메뉴!
with st.expander("⚙️ 예약 조건 설정 및 검색 (클릭하여 열기/닫기)", expanded=True):
    col_input1, col_input2 = st.columns(2)
    
    with col_input1:
        # 🌟 마스터 스위치
        search_all = st.checkbox("🔥 전체 합주실 모두 선택", value=True, help="체크를 해제하면 원하는 합주실만 고를 수 있습니다.")
        
        st.caption("🎸 합주실 목록:")
        user_studios = []
        
        # 🌟 창업자의 킬러 피처: DB 전체 전시 
        for studio_name in STUDIO_DB.keys():
            # search_all이 켜져있으면 -> 체크박스를 강제로 켜고, 회색으로 얼려둠(disabled)
            # search_all이 꺼져있으면 -> 유저가 마음대로 껐다 켰다 조작 가능
            is_checked = st.checkbox(studio_name, value=True, disabled=search_all)
            
            if search_all or is_checked:
                user_studios.append(studio_name)
                
    with col_input2:
        user_date = st.date_input("📅 날짜", value=datetime.date.today(), min_value=datetime.date.today(), format="YYYY.MM.DD")
        time_range = st.slider("⏰ 시간대", 0, 24, (16, 22), 1)
        min_hours = st.number_input("⏳ 최소 연속 시간", min_value=1, max_value=6, value=2)
        
    start_time, end_time = time_range
    required_times = set(range(start_time, end_time))
    
    st.divider()
    search_clicked = st.button("🚀 조건에 맞는 합주실 검색", key="main_btn", use_container_width=True)



# 🌟 (이 부분 삭제: col_map, col_table = st.columns([6, 4]))

# 📺 3. 메인 화면 출력 로직
if not search_clicked:
    # --- 검색 전 초기 화면 ---
    st.subheader("🗺️ 홍대 합주실 맵")
    st.info("👆 위에서 조건을 설정하고 스캔을 시작하세요! (지도 핀을 누르면 예약 가능합니다)")
    
    # 모바일을 위해 지도 높이를 400으로 살짝 줄임 (여백 확보)
    m_default = folium.Map(location=[37.5561, 126.9234], zoom_start=15)
    for studio_name, rooms in STUDIO_DB.items():
        if not rooms: continue
        lat, lon, first_url = rooms[0]["lat"], rooms[0]["lon"], rooms[0]["url"]
        popup_html = f"""<div style="text-align: center;"><h4><b>{studio_name}</b></h4><a href="{first_url}" target="_blank" style="padding: 5px; background-color: #03C75A; color: white; text-decoration: none; border-radius: 5px;">네이버 예약 바로가기</a></div>"""
        folium.Marker([lat, lon], popup=folium.Popup(popup_html, max_width=300), tooltip=studio_name, icon=folium.Icon(color="gray", icon="music", prefix='fa')).add_to(m_default)
    st_folium(m_default, use_container_width=True, height=400, returned_objects=[])

else:
    # --- 검색 실행 화면 ---
    if not user_studios: 
        st.warning("⚠️ 합주실을 최소 1개 이상 선택해주세요!")
        st.stop()

    display_date = user_date.strftime("%m월 %d일")
    with st.spinner(f'{display_date} 스케줄 털어오는 중...'):
        raw_df = run_api_crawler(user_date, tuple(user_studios)) 
    
    filtered_list = []
    for _, row in raw_df.iterrows():
        if row["상태"] == "✅ 예약 가능":
            available_set = convert_to_24h_set(row["예약 가능 시간"])
            matching = required_times & available_set
            
            if matching and check_consecutive_hours(matching, min_hours):
                filtered_list.append({
                  "합주실 이름": row["합주실 이름"], 
                  "🎸 예약 가능": format_time_ranges(matching), 
                  "예약링크": row["예약링크"], 
                  "lat": row["lat"], 
                  "lon": row["lon"], 
                  "studio_name": row["합주실 이름"].split()[0]
                })
    
    if filtered_list:
        st.success(f"🎉 {display_date}, 최소 {min_hours}시간 연속 가능한 방을 찾았습니다!")
        
        # 🌟 창업자의 모바일 UX 해결책: 탭(Tabs) UI 도입!
        tab_list, tab_map = st.tabs(["📋 예약 가능한 방 리스트", "🗺️ 지도에서 위치 보기"])
        
        # 첫 번째 탭: 리스트 (스크롤 막힘없이 바로 결과 확인!)
        with tab_list:
            df_display = pd.DataFrame(filtered_list).drop(columns=["lat", "lon", "studio_name"])
            st.dataframe(
                df_display, 
                use_container_width=True, 
                column_config={"예약링크": st.column_config.LinkColumn("예약 링크", display_text="🔗 예약하기")}
            )

        # 두 번째 탭: 지도 (위치 궁금한 사람만 탭해서 확인)
        with tab_map:
            m_filtered = folium.Map(location=[filtered_list[0]["lat"], filtered_list[0]["lon"]], zoom_start=15)
            location_groups = {}
            for room in filtered_list:
                coord = (room["lat"], room["lon"])
                if coord not in location_groups: location_groups[coord] = {"studio_name": "합주실", "rooms_html": ""}
                for s_name, r_list in STUDIO_DB.items():
                    if any(r["name"] == room["합주실 이름"] for r in r_list):
                        location_groups[coord]["studio_name"] = s_name
                        break
                location_groups[coord]["rooms_html"] += f"<li><b>{room['합주실 이름']}</b> ({room['🎸 예약 가능']}) <a href='{room['예약링크']}' target='_blank'>[예약]</a></li>"

            for coord, data in location_groups.items():
                popup_html = f"""<div><h4 style="color: #E91E63;"><b>{data['studio_name']}</b></h4><ul>{data['rooms_html']}</ul></div>"""
                folium.Marker([coord[0], coord[1]], popup=folium.Popup(popup_html, max_width=350), tooltip=data['studio_name'], icon=folium.Icon(color="red", icon="music", prefix='fa')).add_to(m_filtered)
            st_folium(m_filtered, use_container_width=True, height=400, returned_objects=[])

    else:
        st.error(f"😭 지정한 시간 내에 연속 {min_hours}시간 이상 비어있는 방이 없습니다.")