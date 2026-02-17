"""
数据库模型定义
根据Summo入稿的表头设计，对复合字段进行拆分
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()


class Property(Base):
    """
    物件（房产）数据表
    字段设计参考Summo入稿表头，并对复合字段进行拆分
    """
    __tablename__ = 'properties'

    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True)

    # 物件基本信息（拆分后）
    property_name = Column(String(255), comment='物件名')
    room_number = Column(String(50), comment='号室')

    # 地址信息（拆分后）
    address_prefecture = Column(String(50), comment='都道府県')
    address_city = Column(String(100), comment='市区町村')
    address_detail = Column(String(255), comment='详细住所')

    # 交通信息（拆分后）
    railway_line = Column(String(100), comment='沿線')
    station = Column(String(100), comment='駅')
    walk_minutes = Column(Integer, comment='徒歩分数')

    # 物件详情
    property_type = Column(String(50), comment='物件種別（マンション/アパート等）')
    structure = Column(String(50), comment='構造（RC/鉄骨等）')
    floor = Column(String(20), comment='所在階')
    total_floors = Column(Integer, comment='総階数')
    built_year = Column(Integer, comment='築年')
    built_month = Column(Integer, comment='築月')

    # 面积和间取
    floor_plan = Column(String(50), comment='間取り（1K/2LDK等）')
    area_sqm = Column(Float, comment='専有面積（平米）')
    balcony_area = Column(Float, comment='バルコニー面積（平米）')

    # 费用信息
    rent = Column(Integer, comment='賃料（円）')
    management_fee = Column(Integer, comment='管理費（円）')
    deposit = Column(String(50), comment='敷金')
    key_money = Column(String(50), comment='礼金')

    # 设备和条件
    facilities = Column(Text, comment='設備（JSON形式）')
    conditions = Column(Text, comment='入居条件（JSON形式）')

    # 推定反響数（核心指标）
    estimated_response = Column(Integer, comment='推定反響数（件/月）')
    response_rank = Column(String(20), comment='反響ランク')

    # 其他信息
    available_date = Column(String(50), comment='入居可能日')
    contract_period = Column(String(50), comment='契約期間')
    renewal_fee = Column(String(50), comment='更新料')

    # 原始数据备份
    raw_data = Column(Text, comment='原始HTML或JSON数据')
    source_url = Column(String(500), comment='来源URL')

    # 抓取相关
    area_name = Column(String(100), comment='抓取时的市郡区名')
    scraped_at = Column(DateTime, default=datetime.now, comment='抓取时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    def __repr__(self):
        return f"<Property(id={self.id}, name={self.property_name}, room={self.room_number}, response={self.estimated_response})>"

    def to_dict(self):
        """转换为字典，方便数据分析使用"""
        return {
            'id': self.id,
            'property_name': self.property_name,
            'room_number': self.room_number,
            'address_prefecture': self.address_prefecture,
            'address_city': self.address_city,
            'address_detail': self.address_detail,
            'railway_line': self.railway_line,
            'station': self.station,
            'walk_minutes': self.walk_minutes,
            'property_type': self.property_type,
            'structure': self.structure,
            'floor': self.floor,
            'total_floors': self.total_floors,
            'built_year': self.built_year,
            'built_month': self.built_month,
            'floor_plan': self.floor_plan,
            'area_sqm': self.area_sqm,
            'balcony_area': self.balcony_area,
            'rent': self.rent,
            'management_fee': self.management_fee,
            'deposit': self.deposit,
            'key_money': self.key_money,
            'facilities': self.facilities,
            'conditions': self.conditions,
            'estimated_response': self.estimated_response,
            'response_rank': self.response_rank,
            'available_date': self.available_date,
            'area_name': self.area_name,
            'scraped_at': self.scraped_at,
        }


def get_engine(database_url=None):
    """获取数据库引擎"""
    if database_url is None:
        database_url = os.getenv("DATABASE_URL", "sqlite:///data/properties.db")
    return create_engine(database_url, echo=False)


def get_session(engine=None):
    """获取数据库会话"""
    if engine is None:
        engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def init_db(engine=None):
    """初始化数据库，创建所有表"""
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
    print("数据库初始化完成")
    return engine


if __name__ == "__main__":
    # 测试数据库初始化
    init_db()
