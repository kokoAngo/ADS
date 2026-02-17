"""
从raw_data中重新解析并更新数据库中的缺失字段
"""
import re
import sqlite3
import pandas as pd

def parse_raw_data(raw_text):
    """从原始文本中提取各字段"""
    data = {}

    if not raw_text or pd.isna(raw_text):
        return data

    # 分割为多行处理
    lines = raw_text.split('\n')
    text = raw_text

    # 1. 提取沿線/駅 - 格式: "東京メトロ半蔵門線/半蔵門"
    line_station_match = re.search(r'([^\s]+線)[/／]([^\s\n]+)', text)
    if line_station_match:
        data['railway_line'] = line_station_match.group(1)
        station = line_station_match.group(2).replace('駅', '').strip()
        if station and len(station) < 20:
            data['station'] = station

    # 2. 提取徒歩分数 - 格式: "4分" 或 "徒歩4分"
    walk_match = re.search(r'(?:徒歩)?(\d+)\s*分(?!\s*[以上])', text)
    if walk_match:
        data['walk_minutes'] = int(walk_match.group(1))

    # 3. 提取物件類型 - マンション, アパート, 一戸建て等
    property_types = ['マンション', 'アパート', '一戸建て', 'テラスハウス', 'タウンハウス']
    for pt in property_types:
        if pt in text:
            data['property_type'] = pt
            break

    # 4. 提取敷金/礼金 - 格式: "1ヶ月" 或 "0.5ヶ月" 或 "なし"
    # 通常格式是: 敷金1ヶ月 礼金1ヶ月 或者在连续位置出现两次
    deposit_key_pattern = re.findall(r'([\d.]+)ヶ月', text)
    if len(deposit_key_pattern) >= 2:
        # 通常第一个是敷金，第二个是礼金
        data['deposit'] = deposit_key_pattern[0] + 'ヶ月'
        data['key_money'] = deposit_key_pattern[1] + 'ヶ月'

    # 5. 提取管理費 - 格式: "管理費5,000円" 或 "5000円"
    mgmt_match = re.search(r'管理[費费][：:\s]*([\d,]+)\s*円', text)
    if mgmt_match:
        data['management_fee'] = int(mgmt_match.group(1).replace(',', ''))

    # 6. 提取構造 - RC, SRC, 木造, 鉄骨等
    structures = ['RC', 'SRC', 'S造', '鉄骨', '木造', '鉄筋コンクリート', '軽量鉄骨']
    for struct in structures:
        if struct in text:
            data['structure'] = struct
            break

    # 7. 提取階数 - 格式: "3階/5階建" 或 "3F/5F"
    floor_match = re.search(r'(\d+)\s*[階F][/／](\d+)\s*[階F]?建?', text)
    if floor_match:
        data['floor'] = floor_match.group(1) + '階'  # VARCHAR field
        data['total_floors'] = int(floor_match.group(2))
    else:
        # 尝试只匹配楼层
        floor_only = re.search(r'(\d+)\s*階', text)
        if floor_only:
            data['floor'] = floor_only.group(1) + '階'

    # 8. 提取物件名（如果包含・号室的格式）
    name_match = re.search(r'([^\n\t]+[・・]?\d*号室?)', text)
    if name_match:
        name = name_match.group(1).strip()
        if len(name) > 3 and len(name) < 50:
            # 分离物件名和号室
            room_match = re.search(r'[・・]?(\d+号室?)$', name)
            if room_match:
                data['room_number'] = room_match.group(1)
                data['property_name'] = name[:room_match.start()].rstrip('・・').strip()
            else:
                data['property_name'] = name

    return data


def update_database():
    """更新数据库中的缺失字段"""
    conn = sqlite3.connect('data/properties.db')

    # 读取所有数据
    df = pd.read_sql('SELECT * FROM properties', conn)
    print(f"总记录数: {len(df)}")

    # 统计更新
    updates = {
        'station': 0,
        'walk_minutes': 0,
        'property_type': 0,
        'structure': 0,
        'floor': 0,
        'room_number': 0,
        'deposit': 0,
        'key_money': 0,
        'management_fee': 0,
    }

    cursor = conn.cursor()

    for idx, row in df.iterrows():
        raw_data = row['raw_data']
        if not raw_data:
            continue

        parsed = parse_raw_data(raw_data)
        if not parsed:
            continue

        # 构建UPDATE语句 - 只更新空值字段
        update_fields = []
        update_values = []

        for field, value in parsed.items():
            # 检查当前值是否为空
            current_val = row.get(field)
            if pd.isna(current_val) or current_val is None or current_val == '' or current_val == 0:
                update_fields.append(f"{field} = ?")
                update_values.append(value)
                if field in updates:
                    updates[field] += 1

        if update_fields:
            update_values.append(row['id'])
            sql = f"UPDATE properties SET {', '.join(update_fields)} WHERE id = ?"
            cursor.execute(sql, update_values)

    conn.commit()
    conn.close()

    print("\n更新完成!")
    print("各字段更新数量:")
    for field, count in updates.items():
        print(f"  {field}: {count}")


def export_updated_csv():
    """导出更新后的数据为CSV"""
    conn = sqlite3.connect('data/properties.db')
    df = pd.read_sql('SELECT * FROM properties', conn)
    conn.close()

    # 导出CSV
    df.to_csv('data/properties_updated.csv', index=False, encoding='utf-8-sig')

    # 打印字段填充情况
    print("\n=== 更新后字段填充情况 ===")
    for col in df.columns:
        non_null = df[col].notna().sum()
        # 排除空字符串
        if df[col].dtype == 'object':
            non_null = (df[col].notna() & (df[col] != '')).sum()
        pct = non_null / len(df) * 100
        print(f"{col}: {non_null}/{len(df)} ({pct:.1f}%)")

    print(f"\n已导出到: data/properties_updated.csv")


if __name__ == "__main__":
    import os
    os.chdir(r"D:\Fango Ads")

    print("=== 从raw_data解析缺失字段 ===\n")
    update_database()

    print("\n=== 导出更新后的CSV ===")
    export_updated_csv()
