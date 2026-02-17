"""
物件数据分析模块
使用聚类分析、决策树等方法查找高分物件的特性
"""
import os
import sys
import json
import warnings
from typing import Dict, List, Tuple, Optional

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.cluster import KMeans, DBSCAN
from sklearn.tree import DecisionTreeClassifier, plot_tree
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Property, get_session, get_engine

# 设置matplotlib中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

warnings.filterwarnings('ignore')


class PropertyAnalyzer:
    """物件数据分析类"""

    def __init__(self):
        """初始化分析器"""
        self.session = get_session()
        self.df: Optional[pd.DataFrame] = None
        self.df_processed: Optional[pd.DataFrame] = None
        self.label_encoders: Dict[str, LabelEncoder] = {}
        self.scaler = StandardScaler()

    def load_data(self) -> pd.DataFrame:
        """
        从数据库加载数据
        Returns:
            物件数据DataFrame
        """
        print("正在从数据库加载数据...")
        properties = self.session.query(Property).all()

        if not properties:
            print("数据库中没有数据，请先运行爬虫抓取数据")
            return pd.DataFrame()

        # 转换为DataFrame
        data = [prop.to_dict() for prop in properties]
        self.df = pd.DataFrame(data)

        print(f"加载了 {len(self.df)} 条物件数据")
        print(f"数据列: {list(self.df.columns)}")

        return self.df

    def load_from_csv(self, filepath: str) -> pd.DataFrame:
        """
        从CSV文件加载数据（用于测试）
        Args:
            filepath: CSV文件路径
        Returns:
            物件数据DataFrame
        """
        self.df = pd.read_csv(filepath)
        print(f"从CSV加载了 {len(self.df)} 条数据")
        return self.df

    def preprocess_data(self) -> pd.DataFrame:
        """
        数据预处理
        - 处理缺失值
        - 编码分类变量
        - 标准化数值变量
        Returns:
            处理后的DataFrame
        """
        if self.df is None or self.df.empty:
            raise ValueError("请先加载数据")

        print("\n开始数据预处理...")
        df = self.df.copy()

        # 选择用于分析的特征
        numeric_features = [
            'walk_minutes', 'total_floors', 'built_year',
            'area_sqm', 'rent', 'management_fee',
            'estimated_response'
        ]

        categorical_features = [
            'address_city', 'railway_line', 'station',
            'property_type', 'structure', 'floor_plan'
        ]

        # 筛选存在的列
        numeric_features = [f for f in numeric_features if f in df.columns]
        categorical_features = [f for f in categorical_features if f in df.columns]

        print(f"数值特征: {numeric_features}")
        print(f"分类特征: {categorical_features}")

        # 处理数值特征的缺失值（用中位数填充）
        for col in numeric_features:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                df[col] = df[col].fillna(df[col].median())

        # 处理分类特征的缺失值（用众数填充）
        for col in categorical_features:
            if col in df.columns:
                df[col] = df[col].fillna(df[col].mode().iloc[0] if not df[col].mode().empty else 'Unknown')

        # 编码分类变量
        for col in categorical_features:
            if col in df.columns:
                le = LabelEncoder()
                df[f'{col}_encoded'] = le.fit_transform(df[col].astype(str))
                self.label_encoders[col] = le

        self.df_processed = df
        print("数据预处理完成")

        return df

    def create_response_categories(self) -> pd.DataFrame:
        """
        创建反響数分类
        将推定反響数分为几个等级
        """
        if self.df_processed is None:
            self.preprocess_data()

        df = self.df_processed.copy()

        if 'estimated_response' not in df.columns:
            print("警告：数据中没有 estimated_response 列")
            return df

        # 根据推定反響数分类
        # 高分: 30+件/月
        # 中高: 20-29件/月
        # 中等: 10-19件/月
        # 低分: <10件/月
        def categorize_response(x):
            if pd.isna(x):
                return 'Unknown'
            elif x >= 30:
                return 'High'
            elif x >= 20:
                return 'Medium-High'
            elif x >= 10:
                return 'Medium'
            else:
                return 'Low'

        df['response_category'] = df['estimated_response'].apply(categorize_response)

        # 编码分类
        le = LabelEncoder()
        df['response_category_encoded'] = le.fit_transform(df['response_category'])
        self.label_encoders['response_category'] = le

        self.df_processed = df
        return df

    def perform_clustering(self, n_clusters: int = 4) -> Tuple[pd.DataFrame, np.ndarray]:
        """
        执行K-Means聚类分析
        Args:
            n_clusters: 聚类数量
        Returns:
            带聚类标签的DataFrame和聚类中心
        """
        print(f"\n执行K-Means聚类分析 (k={n_clusters})...")

        if self.df_processed is None:
            self.preprocess_data()

        df = self.df_processed.copy()

        # 选择聚类特征
        cluster_features = []
        potential_features = [
            'walk_minutes', 'area_sqm', 'rent', 'built_year',
            'address_city_encoded', 'floor_plan_encoded'
        ]

        for f in potential_features:
            if f in df.columns and df[f].notna().sum() > 0:
                cluster_features.append(f)

        if len(cluster_features) < 2:
            print("特征不足，无法进行聚类分析")
            return df, np.array([])

        print(f"聚类特征: {cluster_features}")

        # 准备数据
        X = df[cluster_features].copy()
        X = X.fillna(X.median())

        # 标准化
        X_scaled = self.scaler.fit_transform(X)

        # 执行聚类
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        df['cluster'] = kmeans.fit_predict(X_scaled)

        # 分析每个聚类
        print("\n聚类分析结果:")
        for i in range(n_clusters):
            cluster_data = df[df['cluster'] == i]
            print(f"\n聚类 {i} ({len(cluster_data)} 个物件):")

            if 'estimated_response' in df.columns:
                print(f"  平均推定反響数: {cluster_data['estimated_response'].mean():.1f}")

            if 'rent' in df.columns:
                print(f"  平均賃料: {cluster_data['rent'].mean():,.0f} 円")

            if 'area_sqm' in df.columns:
                print(f"  平均面積: {cluster_data['area_sqm'].mean():.1f} ㎡")

            if 'walk_minutes' in df.columns:
                print(f"  平均徒歩分数: {cluster_data['walk_minutes'].mean():.1f} 分")

        self.df_processed = df
        return df, kmeans.cluster_centers_

    def build_decision_tree(self) -> Tuple[DecisionTreeClassifier, Dict]:
        """
        构建决策树分类器
        用于识别高分物件的特征规则
        Returns:
            决策树模型和特征重要性
        """
        print("\n构建决策树分类器...")

        if self.df_processed is None:
            self.preprocess_data()

        self.create_response_categories()
        df = self.df_processed.copy()

        # 准备特征
        feature_cols = []
        potential_features = [
            'walk_minutes', 'area_sqm', 'rent', 'built_year',
            'management_fee', 'total_floors',
            'address_city_encoded', 'floor_plan_encoded',
            'railway_line_encoded', 'structure_encoded'
        ]

        for f in potential_features:
            if f in df.columns and df[f].notna().sum() > len(df) * 0.5:
                feature_cols.append(f)

        if len(feature_cols) < 2 or 'response_category_encoded' not in df.columns:
            print("特征不足或目标变量缺失，无法构建决策树")
            return None, {}

        print(f"使用特征: {feature_cols}")

        # 准备数据
        X = df[feature_cols].fillna(0)
        y = df['response_category_encoded']

        # 分割数据
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        # 训练决策树
        dt = DecisionTreeClassifier(
            max_depth=5,
            min_samples_split=10,
            min_samples_leaf=5,
            random_state=42
        )
        dt.fit(X_train, y_train)

        # 评估模型
        train_score = dt.score(X_train, y_train)
        test_score = dt.score(X_test, y_test)
        print(f"\n模型评估:")
        print(f"  训练集准确率: {train_score:.3f}")
        print(f"  测试集准确率: {test_score:.3f}")

        # 特征重要性
        feature_importance = dict(zip(feature_cols, dt.feature_importances_))
        print("\n特征重要性排序:")
        for feature, importance in sorted(feature_importance.items(), key=lambda x: x[1], reverse=True):
            if importance > 0.01:
                print(f"  {feature}: {importance:.3f}")

        # 交叉验证
        cv_scores = cross_val_score(dt, X, y, cv=5)
        print(f"\n交叉验证得分: {cv_scores.mean():.3f} (+/- {cv_scores.std() * 2:.3f})")

        return dt, feature_importance

    def build_binary_classification_tree(self, threshold: float = 5.0) -> Tuple[DecisionTreeClassifier, Dict]:
        """
        构建二分类决策树
        将物件分为"高反響"和"低反響"两类

        Args:
            threshold: 反響数阈值，>=threshold为高反響
        Returns:
            决策树模型和评估结果
        """
        print(f"\n构建二分类决策树 (阈值: {threshold} 件/月)...")

        if self.df_processed is None:
            self.preprocess_data()

        df = self.df_processed.copy()

        if 'estimated_response' not in df.columns:
            print("警告：数据中没有 estimated_response 列")
            return None, {}

        # 创建二分类目标变量
        df['is_high_response'] = (df['estimated_response'] >= threshold).astype(int)

        # 统计类别分布
        high_count = df['is_high_response'].sum()
        low_count = len(df) - high_count
        print(f"高反響物件: {high_count} ({high_count/len(df)*100:.1f}%)")
        print(f"低反響物件: {low_count} ({low_count/len(df)*100:.1f}%)")

        # 准备特征
        feature_cols = []
        feature_names = []  # 用于可视化的中文名称
        potential_features = {
            'walk_minutes': '駅徒歩(分)',
            'area_sqm': '面積(㎡)',
            'rent': '賃料(円)',
            'built_year': '築年',
            'management_fee': '管理費',
            'total_floors': '総階数',
            'address_city_encoded': '区域',
            'floor_plan_encoded': '間取り',
        }

        for f, name in potential_features.items():
            if f in df.columns and df[f].notna().sum() > len(df) * 0.5:
                feature_cols.append(f)
                feature_names.append(name)

        if len(feature_cols) < 2:
            print("特征不足，无法构建决策树")
            return None, {}

        print(f"使用特征: {feature_names}")

        # 准备数据
        X = df[feature_cols].fillna(0)
        y = df['is_high_response']

        # 分割数据
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # 训练决策树（限制深度便于可视化）
        dt = DecisionTreeClassifier(
            max_depth=4,
            min_samples_split=50,
            min_samples_leaf=20,
            random_state=42,
            class_weight='balanced'  # 处理类别不平衡
        )
        dt.fit(X_train, y_train)

        # 评估模型
        train_score = dt.score(X_train, y_train)
        test_score = dt.score(X_test, y_test)

        # 预测并生成分类报告
        y_pred = dt.predict(X_test)

        print(f"\n===== 模型评估结果 =====")
        print(f"训练集准确率: {train_score:.1%}")
        print(f"测试集准确率: {test_score:.1%}")

        print(f"\n分类报告:")
        print(classification_report(y_test, y_pred, target_names=['低反響', '高反響']))

        # 混淆矩阵
        cm = confusion_matrix(y_test, y_pred)
        print(f"\n混淆矩阵:")
        print(f"              预测低反響  预测高反響")
        print(f"实际低反響      {cm[0][0]:5d}      {cm[0][1]:5d}")
        print(f"实际高反響      {cm[1][0]:5d}      {cm[1][1]:5d}")

        # 特征重要性
        feature_importance = dict(zip(feature_names, dt.feature_importances_))
        print("\n特征重要性排序:")
        for feature, importance in sorted(feature_importance.items(), key=lambda x: x[1], reverse=True):
            if importance > 0.01:
                print(f"  {feature}: {importance:.1%}")

        # 交叉验证
        cv_scores = cross_val_score(dt, X, y, cv=5, scoring='accuracy')
        print(f"\n5折交叉验证准确率: {cv_scores.mean():.1%} (+/- {cv_scores.std() * 2:.1%})")

        # 可视化决策树
        output_dir = "data/analysis"
        os.makedirs(output_dir, exist_ok=True)

        plt.figure(figsize=(20, 10))
        plot_tree(
            dt,
            feature_names=feature_names,
            class_names=['低反響', '高反響'],
            filled=True,
            rounded=True,
            fontsize=10
        )
        plt.title(f'分类决策树 (阈值: {threshold}件/月)', fontsize=14)
        tree_path = f"{output_dir}/classification_tree.png"
        plt.savefig(tree_path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"\n决策树图已保存: {tree_path}")

        # 保存评估结果
        results = {
            'threshold': threshold,
            'train_accuracy': train_score,
            'test_accuracy': test_score,
            'cv_accuracy_mean': cv_scores.mean(),
            'cv_accuracy_std': cv_scores.std(),
            'feature_importance': feature_importance,
            'confusion_matrix': cm.tolist(),
            'high_response_count': high_count,
            'low_response_count': low_count
        }

        return dt, results

    def build_random_forest(self) -> Tuple[RandomForestClassifier, Dict]:
        """
        构建随机森林分类器
        Returns:
            随机森林模型和特征重要性
        """
        print("\n构建随机森林分类器...")

        if self.df_processed is None:
            self.preprocess_data()

        self.create_response_categories()
        df = self.df_processed.copy()

        # 准备特征
        feature_cols = []
        potential_features = [
            'walk_minutes', 'area_sqm', 'rent', 'built_year',
            'management_fee', 'total_floors',
            'address_city_encoded', 'floor_plan_encoded',
            'railway_line_encoded', 'structure_encoded'
        ]

        for f in potential_features:
            if f in df.columns and df[f].notna().sum() > len(df) * 0.5:
                feature_cols.append(f)

        if len(feature_cols) < 2 or 'response_category_encoded' not in df.columns:
            print("特征不足，无法构建随机森林")
            return None, {}

        # 准备数据
        X = df[feature_cols].fillna(0)
        y = df['response_category_encoded']

        # 分割数据
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        # 训练随机森林
        rf = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=10,
            random_state=42
        )
        rf.fit(X_train, y_train)

        # 评估
        train_score = rf.score(X_train, y_train)
        test_score = rf.score(X_test, y_test)
        print(f"\n模型评估:")
        print(f"  训练集准确率: {train_score:.3f}")
        print(f"  测试集准确率: {test_score:.3f}")

        # 特征重要性
        feature_importance = dict(zip(feature_cols, rf.feature_importances_))
        print("\n特征重要性排序:")
        for feature, importance in sorted(feature_importance.items(), key=lambda x: x[1], reverse=True):
            if importance > 0.01:
                print(f"  {feature}: {importance:.3f}")

        return rf, feature_importance

    def analyze_high_response_properties(self) -> Dict:
        """
        分析高反响物件的共性特征
        Returns:
            高反响物件特征总结
        """
        print("\n分析高反响物件特征...")

        if self.df_processed is None:
            self.preprocess_data()

        self.create_response_categories()
        df = self.df_processed.copy()

        # 筛选高反响物件（20件以上/月）
        high_response = df[df['estimated_response'] >= 20] if 'estimated_response' in df.columns else df
        all_properties = df

        if len(high_response) == 0:
            print("没有高反响物件数据")
            return {}

        print(f"\n高反响物件数量: {len(high_response)} / {len(all_properties)} ({len(high_response)/len(all_properties)*100:.1f}%)")

        insights = {
            'high_response_count': len(high_response),
            'total_count': len(all_properties),
            'features': {}
        }

        # 分析各特征
        numeric_cols = ['walk_minutes', 'area_sqm', 'rent', 'built_year', 'management_fee']
        categorical_cols = ['address_city', 'floor_plan', 'railway_line', 'station']

        print("\n--- 数值特征对比 ---")
        for col in numeric_cols:
            if col in df.columns and df[col].notna().sum() > 0:
                high_mean = high_response[col].mean()
                all_mean = all_properties[col].mean()
                diff_pct = (high_mean - all_mean) / all_mean * 100 if all_mean != 0 else 0

                insights['features'][col] = {
                    'high_response_mean': high_mean,
                    'all_mean': all_mean,
                    'diff_percent': diff_pct
                }

                print(f"\n{col}:")
                print(f"  高反响物件平均: {high_mean:.2f}")
                print(f"  全体平均: {all_mean:.2f}")
                print(f"  差异: {diff_pct:+.1f}%")

        print("\n--- 分类特征分布 ---")
        for col in categorical_cols:
            if col in df.columns and df[col].notna().sum() > 0:
                # 高反响物件的分布
                high_dist = high_response[col].value_counts(normalize=True).head(5)
                all_dist = all_properties[col].value_counts(normalize=True).head(5)

                insights['features'][col] = {
                    'high_response_top5': high_dist.to_dict(),
                    'all_top5': all_dist.to_dict()
                }

                print(f"\n{col} (高反响物件 Top 5):")
                for val, pct in high_dist.items():
                    print(f"  {val}: {pct*100:.1f}%")

        return insights

    def visualize_results(self, output_dir: str = "data/analysis"):
        """
        生成可视化图表
        Args:
            output_dir: 输出目录
        """
        os.makedirs(output_dir, exist_ok=True)

        if self.df_processed is None:
            self.preprocess_data()

        df = self.df_processed.copy()

        # 1. 推定反響数分布
        if 'estimated_response' in df.columns:
            plt.figure(figsize=(10, 6))
            plt.hist(df['estimated_response'].dropna(), bins=30, edgecolor='black')
            plt.xlabel('推定反響数（件/月）')
            plt.ylabel('物件数')
            plt.title('推定反響数分布')
            plt.savefig(f"{output_dir}/response_distribution.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/response_distribution.png")

        # 2. 租金 vs 反響数
        if 'rent' in df.columns and 'estimated_response' in df.columns:
            plt.figure(figsize=(10, 6))
            plt.scatter(df['rent']/10000, df['estimated_response'], alpha=0.5)
            plt.xlabel('賃料（万円）')
            plt.ylabel('推定反響数（件/月）')
            plt.title('賃料 vs 推定反響数')
            plt.savefig(f"{output_dir}/rent_vs_response.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/rent_vs_response.png")

        # 3. 面積 vs 反響数
        if 'area_sqm' in df.columns and 'estimated_response' in df.columns:
            plt.figure(figsize=(10, 6))
            plt.scatter(df['area_sqm'], df['estimated_response'], alpha=0.5)
            plt.xlabel('専有面積（㎡）')
            plt.ylabel('推定反響数（件/月）')
            plt.title('面積 vs 推定反響数')
            plt.savefig(f"{output_dir}/area_vs_response.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/area_vs_response.png")

        # 4. 徒歩分数 vs 反響数
        if 'walk_minutes' in df.columns and 'estimated_response' in df.columns:
            plt.figure(figsize=(10, 6))
            plt.scatter(df['walk_minutes'], df['estimated_response'], alpha=0.5)
            plt.xlabel('駅徒歩（分）')
            plt.ylabel('推定反響数（件/月）')
            plt.title('駅徒歩分数 vs 推定反響数')
            plt.savefig(f"{output_dir}/walk_vs_response.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/walk_vs_response.png")

        # 5. 区域别反響数
        if 'address_city' in df.columns and 'estimated_response' in df.columns:
            plt.figure(figsize=(14, 8))
            area_response = df.groupby('address_city')['estimated_response'].mean().sort_values(ascending=False)
            area_response.head(20).plot(kind='bar')
            plt.xlabel('市区町村')
            plt.ylabel('平均推定反響数（件/月）')
            plt.title('区域别平均推定反響数（Top 20）')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(f"{output_dir}/area_response.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/area_response.png")

        # 6. 間取り别反響数
        if 'floor_plan' in df.columns and 'estimated_response' in df.columns:
            plt.figure(figsize=(12, 6))
            layout_response = df.groupby('floor_plan')['estimated_response'].mean().sort_values(ascending=False)
            layout_response.head(15).plot(kind='bar')
            plt.xlabel('間取り')
            plt.ylabel('平均推定反響数（件/月）')
            plt.title('間取り别平均推定反響数')
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(f"{output_dir}/layout_response.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/layout_response.png")

        # 7. 相关性热力图
        numeric_cols = ['walk_minutes', 'area_sqm', 'rent', 'built_year',
                       'management_fee', 'estimated_response']
        available_cols = [c for c in numeric_cols if c in df.columns]

        if len(available_cols) >= 3:
            plt.figure(figsize=(10, 8))
            corr_matrix = df[available_cols].corr()
            sns.heatmap(corr_matrix, annot=True, cmap='coolwarm', center=0)
            plt.title('特征相关性热力图')
            plt.tight_layout()
            plt.savefig(f"{output_dir}/correlation_heatmap.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/correlation_heatmap.png")

        # 8. 聚类结果可视化
        if 'cluster' in df.columns and 'rent' in df.columns and 'estimated_response' in df.columns:
            plt.figure(figsize=(10, 6))
            scatter = plt.scatter(
                df['rent']/10000,
                df['estimated_response'],
                c=df['cluster'],
                cmap='viridis',
                alpha=0.6
            )
            plt.colorbar(scatter, label='聚类')
            plt.xlabel('賃料（万円）')
            plt.ylabel('推定反響数（件/月）')
            plt.title('聚类结果可视化')
            plt.savefig(f"{output_dir}/cluster_visualization.png", dpi=150, bbox_inches='tight')
            plt.close()
            print(f"保存: {output_dir}/cluster_visualization.png")

        print(f"\n所有图表已保存到 {output_dir}")

    def generate_report(self, output_path: str = "data/analysis_report.txt") -> str:
        """
        生成分析报告
        Args:
            output_path: 报告输出路径
        Returns:
            报告内容
        """
        print("\n生成分析报告...")

        report = []
        report.append("=" * 60)
        report.append("物件数据分析报告")
        report.append("=" * 60)
        report.append("")

        if self.df_processed is None:
            self.preprocess_data()

        df = self.df_processed

        # 基本统计
        report.append("一、数据概览")
        report.append("-" * 40)
        report.append(f"总物件数: {len(df)}")

        if 'estimated_response' in df.columns:
            report.append(f"平均推定反響数: {df['estimated_response'].mean():.1f} 件/月")
            report.append(f"最高推定反響数: {df['estimated_response'].max():.0f} 件/月")
            report.append(f"高反响物件（≥20件/月）: {len(df[df['estimated_response'] >= 20])} 个")

        if 'rent' in df.columns:
            report.append(f"平均賃料: {df['rent'].mean():,.0f} 円")

        if 'area_sqm' in df.columns:
            report.append(f"平均面積: {df['area_sqm'].mean():.1f} ㎡")

        report.append("")

        # 高反响物件分析
        insights = self.analyze_high_response_properties()
        if insights:
            report.append("二、高反響物件特征分析")
            report.append("-" * 40)

            for feature, data in insights.get('features', {}).items():
                if isinstance(data, dict) and 'diff_percent' in data:
                    report.append(f"\n{feature}:")
                    report.append(f"  高反響物件平均: {data['high_response_mean']:.2f}")
                    report.append(f"  全体平均: {data['all_mean']:.2f}")
                    report.append(f"  差异: {data['diff_percent']:+.1f}%")

        report.append("")

        # 模型分析结果
        report.append("三、机器学习分析")
        report.append("-" * 40)

        dt, dt_importance = self.build_decision_tree()
        if dt_importance:
            report.append("\n决策树特征重要性:")
            for feature, importance in sorted(dt_importance.items(), key=lambda x: x[1], reverse=True)[:5]:
                report.append(f"  {feature}: {importance:.3f}")

        rf, rf_importance = self.build_random_forest()
        if rf_importance:
            report.append("\n随机森林特征重要性:")
            for feature, importance in sorted(rf_importance.items(), key=lambda x: x[1], reverse=True)[:5]:
                report.append(f"  {feature}: {importance:.3f}")

        report.append("")
        report.append("四、结论与建议")
        report.append("-" * 40)
        report.append("根据分析结果，高反響物件通常具有以下特征：")
        report.append("1. 交通便利（靠近车站）")
        report.append("2. 价格合理（性价比高）")
        report.append("3. 热门区域（如港区、渋谷区等）")
        report.append("4. 合适的间取り（根据区域需求）")
        report.append("")
        report.append("=" * 60)

        # 保存报告
        report_text = "\n".join(report)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_text)

        print(f"报告已保存到: {output_path}")
        return report_text

    def run_full_analysis(self):
        """
        运行完整分析流程
        """
        print("开始完整分析流程...")

        # 1. 加载数据
        self.load_data()
        if self.df is None or self.df.empty:
            print("没有数据可分析")
            return

        # 2. 预处理
        self.preprocess_data()

        # 3. 创建分类
        self.create_response_categories()

        # 4. 聚类分析
        self.perform_clustering()

        # 5. 决策树
        self.build_decision_tree()

        # 6. 随机森林
        self.build_random_forest()

        # 7. 高反响物件分析
        self.analyze_high_response_properties()

        # 8. 可视化
        self.visualize_results()

        # 9. 生成报告
        self.generate_report()

        print("\n分析完成!")


def main():
    """主函数"""
    analyzer = PropertyAnalyzer()

    try:
        analyzer.run_full_analysis()
    except Exception as e:
        print(f"分析过程出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
