"""
综合评分推荐系统
基于多维度指标推荐最适合广告的物件
"""
import os
import requests
from dotenv import load_dotenv

os.chdir(r"D:\Fango Ads")
load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "ntn_u754288580510OTZ1AbHOcBNrbctyy3cVt7LNbvNSD752Q")
DATABASE_ID = "3031c197-4dad-800b-917d-d09b8602ec39"

headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Content-Type': 'application/json',
    'Notion-Version': '2022-06-28'
}

# 评分权重配置
WEIGHTS = {
    'view_score': 0.30,      # 予測_view数权重
    'inquiry_score': 0.25,   # 予測_反響数权重
    'competition': 0.25,     # 広告数（竞争度）权重
    'market_rank': 0.20,     # 市場順位权重
}

# 筛选阈值
MIN_VIEW_SCORE = 6.0


def fetch_all_properties():
    """获取所有物件"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        data = response.json()

        if "results" in data:
            all_results.extend(data["results"])
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        else:
            print(f"Error: {data}")
            break

    return all_results


def extract_property_data(page):
    """提取物件数据"""
    props = page.get("properties", {})
    data = {"page_id": page["id"]}

    # REINS_ID
    if "REINS_ID" in props and props["REINS_ID"]["title"]:
        data["reins_id"] = props["REINS_ID"]["title"][0]["plain_text"]

    # 数字字段
    number_fields = {
        "予測_view数": "view_score",
        "予測_反響数": "inquiry_score",
        "広告数": "ad_count",
        "賃料": "rent",
        "専有面積": "area",
        "徒歩分数": "walk_minutes",
    }
    for jp_name, en_name in number_fields.items():
        if jp_name in props and props[jp_name].get("number") is not None:
            data[en_name] = props[jp_name]["number"]

    # 文本字段
    text_fields = {
        "所在地": "address",
        "間取り": "floor_plan",
        "管理会社": "management_company",
        "交通1_沿線名": "railway",
        "交通1_駅名": "station",
    }
    for jp_name, en_name in text_fields.items():
        if jp_name in props:
            prop = props[jp_name]
            if prop.get("rich_text") and prop["rich_text"]:
                data[en_name] = prop["rich_text"][0]["plain_text"]

    # 広告可（select）
    if "広告可" in props and props["広告可"].get("select"):
        data["ad_status"] = props["広告可"]["select"]["name"]
    else:
        data["ad_status"] = None

    return data


def calculate_score(prop):
    """计算综合得分"""
    # 获取各指标值
    view_score = prop.get('view_score', 0) or 0
    inquiry_score = prop.get('inquiry_score', 0) or 0
    ad_count = prop.get('ad_count', 10) or 10  # 默认假设竞争激烈

    # 归一化处理
    # view_score: 0-10分，直接使用
    norm_view = min(view_score / 10, 1.0) * 10

    # inquiry_score: 0-5分左右，归一化到10分
    norm_inquiry = min(inquiry_score / 5, 1.0) * 10

    # ad_count: 竞争度，越少越好 (1-30 → 10-0)
    # 広告数=1 → 竞争得分=10, 広告数=20 → 竞争得分=0
    competition_score = max(0, 10 - (ad_count - 1) * 0.5)

    # 市場順位暂时没有存储，用view_score作为替代指标
    market_score = norm_view

    # 加权计算
    total_score = (
        norm_view * WEIGHTS['view_score'] +
        norm_inquiry * WEIGHTS['inquiry_score'] +
        competition_score * WEIGHTS['competition'] +
        market_score * WEIGHTS['market_rank']
    )

    # 存储分项得分
    prop['norm_view'] = round(norm_view, 2)
    prop['norm_inquiry'] = round(norm_inquiry, 2)
    prop['competition_score'] = round(competition_score, 2)
    prop['total_score'] = round(total_score, 2)

    return total_score


def update_notion_score(page_id, score):
    """更新Notion的推薦点数"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    data = {
        "properties": {
            "推薦点数": {"number": score}
        }
    }
    try:
        response = requests.patch(url, headers=headers, json=data, timeout=60)
        return response.status_code == 200
    except:
        return False


def main():
    print("=" * 70)
    print("物件综合评分推荐系统")
    print("=" * 70)

    # 获取所有物件
    print("\n1. 获取物件数据...")
    pages = fetch_all_properties()
    print(f"   共 {len(pages)} 个物件")

    # 提取数据
    properties = []
    for page in pages:
        prop = extract_property_data(page)
        properties.append(prop)

    # 筛选条件
    print(f"\n2. 筛选条件:")
    print(f"   - 予測_view数 >= {MIN_VIEW_SCORE}")
    print(f"   - 広告可 ≠ 不可（仲介）/ 不可（物件）")

    filtered = []
    for prop in properties:
        # 筛选view分数
        view_score = prop.get('view_score', 0) or 0
        if view_score < MIN_VIEW_SCORE:
            continue

        # 筛选広告可状态
        ad_status = prop.get('ad_status')
        if ad_status in ['不可（仲介）', '不可（物件）']:
            continue

        filtered.append(prop)

    print(f"   筛选后: {len(filtered)} 个物件")

    # 计算得分
    print(f"\n3. 计算综合得分...")
    print(f"   权重: view={WEIGHTS['view_score']}, inquiry={WEIGHTS['inquiry_score']}, "
          f"competition={WEIGHTS['competition']}, market={WEIGHTS['market_rank']}")

    for prop in filtered:
        calculate_score(prop)

    # 排序
    filtered.sort(key=lambda x: x['total_score'], reverse=True)

    # 输出TOP推荐
    print(f"\n4. TOP 推荐物件:")
    print("-" * 70)
    print(f"{'排名':<4} {'REINS_ID':<14} {'综合':<6} {'view':<6} {'反響':<6} {'競争':<6} {'広告数':<6} {'状态'}")
    print("-" * 70)

    for i, prop in enumerate(filtered[:20]):
        reins_id = prop.get('reins_id', 'N/A')
        total = prop.get('total_score', 0)
        view = prop.get('view_score', 0) or 0
        inquiry = prop.get('inquiry_score', 0) or 0
        ad_count = prop.get('ad_count', '-')
        ad_status = prop.get('ad_status') or '未確認'

        # 状态简化显示
        if ad_status == '確認待ち':
            status_disp = '待確認'
        elif ad_status is None:
            status_disp = '未設定'
        else:
            status_disp = ad_status[:6]

        print(f"{i+1:<4} {reins_id:<14} {total:<6.1f} {view:<6.1f} {inquiry:<6.1f} "
              f"{prop.get('competition_score', 0):<6.1f} {str(ad_count):<6} {status_disp}")

    # 写入Notion
    print(f"\n" + "=" * 70)

    # 自动写入TOP物件
    print("\n5. 写入推薦点数到Notion...")
    success = 0
    for prop in filtered:
        if update_notion_score(prop['page_id'], prop['total_score']):
            success += 1

    print(f"   成功更新 {success}/{len(filtered)} 个物件")

    # 输出详细信息
    print(f"\n6. TOP 5 详细信息:")
    print("=" * 70)
    for i, prop in enumerate(filtered[:5]):
        print(f"\n【第{i+1}名】{prop.get('reins_id', 'N/A')}")
        print(f"  综合得分: {prop.get('total_score', 0):.1f}")
        print(f"  予測view数: {prop.get('view_score', 0):.1f}")
        print(f"  予測反響数: {prop.get('inquiry_score', 0):.1f}")
        print(f"  広告数: {prop.get('ad_count', 'N/A')} (競争得分: {prop.get('competition_score', 0):.1f})")
        print(f"  賃料: ¥{prop.get('rent', 0):,.0f}")
        print(f"  面積: {prop.get('area', 0)}㎡")
        print(f"  所在地: {prop.get('address', 'N/A')}")
        print(f"  管理会社: {prop.get('management_company', 'N/A')}")
        print(f"  広告可: {prop.get('ad_status') or '未設定'}")

    print(f"\n" + "=" * 70)
    print("完成!")


if __name__ == "__main__":
    main()
