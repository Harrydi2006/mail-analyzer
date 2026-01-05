#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮件智能日程管理系统 - 功能测试脚本

用于测试系统各个模块的功能是否正常工作
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.core.config import Config
from src.core.logger import setup_logger
from src.models.database import init_database, EmailModel, EventModel
from src.services.email_service import EmailService
from src.services.ai_service import AIService
from src.services.scheduler_service import SchedulerService
from src.services.notion_service import NotionService


class SystemTester:
    """系统测试类"""
    
    def __init__(self):
        """初始化测试器"""
        self.config = Config()
        self.logger = setup_logger()
        self.test_results = []
        
        print("🚀 邮件智能日程管理系统功能测试")
        print("=" * 50)
    
    def run_all_tests(self):
        """运行所有测试"""
        tests = [
            ("配置系统测试", self.test_config_system),
            ("数据库系统测试", self.test_database_system),
            ("邮件服务测试", self.test_email_service),
            ("AI服务测试", self.test_ai_service),
            ("日程服务测试", self.test_scheduler_service),
            ("Notion服务测试", self.test_notion_service),
            ("集成测试", self.test_integration)
        ]
        
        for test_name, test_func in tests:
            print(f"\n📋 {test_name}")
            print("-" * 30)
            
            try:
                result = test_func()
                self.test_results.append((test_name, "PASS" if result else "FAIL", None))
                print(f"✅ {test_name}: {'通过' if result else '失败'}")
            except Exception as e:
                self.test_results.append((test_name, "ERROR", str(e)))
                print(f"❌ {test_name}: 错误 - {e}")
        
        self.print_summary()
    
    def test_config_system(self):
        """测试配置系统"""
        try:
            # 测试配置加载
            app_config = self.config.app_config
            print(f"  应用名称: {app_config.get('name', 'Unknown')}")
            print(f"  版本: {app_config.get('version', 'Unknown')}")
            
            # 测试配置设置和获取
            test_key = "test.key"
            test_value = "test_value"
            self.config.set(test_key, test_value)
            retrieved_value = self.config.get(test_key)
            
            if retrieved_value != test_value:
                print(f"  ❌ 配置设置/获取失败: 期望 {test_value}, 实际 {retrieved_value}")
                return False
            
            print("  ✅ 配置系统正常")
            return True
            
        except Exception as e:
            print(f"  ❌ 配置系统错误: {e}")
            return False
    
    def test_database_system(self):
        """测试数据库系统"""
        try:
            # 初始化数据库
            init_database(self.config)
            print("  ✅ 数据库初始化成功")
            
            # 测试邮件模型
            email_model = EmailModel(self.config)
            test_email = {
                'message_id': 'test-message-id-' + str(datetime.now().timestamp()),
                'subject': '测试邮件主题',
                'sender': 'test@example.com',
                'recipient': 'user@example.com',
                'content': '这是一个测试邮件内容',
                'received_date': datetime.now(),
                'importance_level': 'normal'
            }
            
            email_id = email_model.save_email(test_email)
            if email_id:
                print(f"  ✅ 邮件保存成功，ID: {email_id}")
                
                # 测试邮件检索
                retrieved_email = email_model.get_email_by_id(email_id)
                if retrieved_email:
                    print("  ✅ 邮件检索成功")
                else:
                    print("  ❌ 邮件检索失败")
                    return False
            else:
                print("  ❌ 邮件保存失败")
                return False
            
            # 测试事件模型
            from src.models.database import EventModel
            event_model = EventModel(self.config)
            test_event = {
                'email_id': email_id,
                'title': '测试事件',
                'description': '这是一个测试事件',
                'start_time': datetime.now() + timedelta(days=1),
                'importance_level': 'important',
                'color': '#FF4444'
            }
            
            event_id = event_model.save_event(test_event)
            if event_id:
                print(f"  ✅ 事件保存成功，ID: {event_id}")
            else:
                print("  ❌ 事件保存失败")
                return False
            
            return True
            
        except Exception as e:
            print(f"  ❌ 数据库系统错误: {e}")
            return False
    
    def test_email_service(self):
        """测试邮件服务"""
        try:
            email_service = EmailService(self.config)
            
            # 检查邮件配置
            email_config = self.config.email_config
            if not email_config.get('username') or not email_config.get('password'):
                print("  ⚠️  邮件配置未完成，跳过连接测试")
                print("  ✅ 邮件服务初始化成功")
                return True
            
            # 测试邮件连接（如果配置了的话）
            print("  🔍 测试邮件服务器连接...")
            connection_result = email_service.test_connection()
            
            if connection_result:
                print("  ✅ 邮件服务器连接成功")
            else:
                print("  ❌ 邮件服务器连接失败")
                return False
            
            return True
            
        except Exception as e:
            print(f"  ❌ 邮件服务错误: {e}")
            return False
    
    def test_ai_service(self):
        """测试AI服务"""
        try:
            ai_service = AIService(self.config)
            
            # 检查AI配置
            ai_config = self.config.ai_config
            if not ai_config.get('api_key'):
                print("  ⚠️  AI API密钥未配置，跳过AI测试")
                print("  ✅ AI服务初始化成功")
                return True
            
            # 测试AI分析
            print("  🤖 测试AI邮件分析...")
            test_content = "明天下午2点有一个重要的期末考试，地点在教学楼A101，请大家准时参加。"
            test_subject = "期末考试通知"
            
            analysis_result = ai_service.analyze_email_content(test_content, test_subject)
            
            if analysis_result and analysis_result.get('summary'):
                print(f"  ✅ AI分析成功")
                print(f"    总结: {analysis_result['summary']}")
                print(f"    重要性评分: {analysis_result.get('importance_score', 'N/A')}")
                print(f"    提取事件数: {len(analysis_result.get('events', []))}")
                
                if analysis_result.get('events'):
                    for i, event in enumerate(analysis_result['events'], 1):
                        print(f"    事件{i}: {event.get('title', 'N/A')} - {event.get('start_time', 'N/A')}")
                
                return True
            else:
                print("  ❌ AI分析失败")
                return False
            
        except Exception as e:
            print(f"  ❌ AI服务错误: {e}")
            return False
    
    def test_scheduler_service(self):
        """测试日程服务"""
        try:
            scheduler_service = SchedulerService(self.config)
            
            # 测试添加事件
            test_event = {
                'title': '测试日程事件',
                'description': '这是一个测试的日程事件',
                'start_time': datetime.now() + timedelta(hours=2),
                'end_time': datetime.now() + timedelta(hours=3),
                'location': '测试地点',
                'importance_level': 'important'
            }
            
            event_id = scheduler_service.add_event(test_event)
            if event_id:
                print(f"  ✅ 事件添加成功，ID: {event_id}")
            else:
                print("  ❌ 事件添加失败")
                return False
            
            # 测试获取即将到来的事件
            upcoming_events = scheduler_service.get_upcoming_events(7)
            print(f"  ✅ 获取到 {len(upcoming_events)} 个即将到来的事件")
            
            # 测试事件统计
            stats = scheduler_service.get_event_statistics()
            print(f"  ✅ 事件统计: 总计 {stats.get('total_events', 0)} 个事件")
            
            return True
            
        except Exception as e:
            print(f"  ❌ 日程服务错误: {e}")
            return False
    
    def test_notion_service(self):
        """测试Notion服务"""
        try:
            notion_service = NotionService(self.config)
            
            # 检查Notion配置
            notion_config = self.config.notion_config
            if not notion_config.get('token'):
                print("  ⚠️  Notion Token未配置，跳过Notion测试")
                print("  ✅ Notion服务初始化成功")
                return True
            
            # 测试Notion连接
            print("  📚 测试Notion连接...")
            connection_result = notion_service.test_connection()
            
            if connection_result.get('success'):
                print(f"  ✅ Notion连接成功")
                if connection_result.get('user'):
                    print(f"    用户: {connection_result['user']}")
                if connection_result.get('database'):
                    print(f"    数据库: {connection_result['database']}")
            else:
                print(f"  ❌ Notion连接失败: {connection_result.get('error', 'Unknown error')}")
                return False
            
            return True
            
        except Exception as e:
            print(f"  ❌ Notion服务错误: {e}")
            return False
    
    def test_integration(self):
        """测试系统集成"""
        try:
            print("  🔗 测试系统集成流程...")
            
            # 模拟完整的邮件处理流程
            email_service = EmailService(self.config)
            ai_service = AIService(self.config)
            scheduler_service = SchedulerService(self.config)
            notion_service = NotionService(self.config)
            
            # 1. 模拟邮件数据
            mock_email = {
                'message_id': 'integration-test-' + str(datetime.now().timestamp()),
                'subject': '重要会议通知 - 项目评审',
                'sender': 'manager@company.com',
                'recipient': 'user@company.com',
                'content': '请注意，明天（2024年1月15日）下午3点在会议室B201举行项目评审会议，请准时参加。会议预计持续2小时。',
                'received_date': datetime.now(),
                'importance_level': 'important'
            }
            
            print("    1. 处理模拟邮件数据...")
            
            # 2. AI分析（如果配置了）
            analysis_result = None
            if self.config.ai_config.get('api_key'):
                print("    2. AI分析邮件内容...")
                analysis_result = ai_service.analyze_email_content(
                    mock_email['content'], 
                    mock_email['subject']
                )
                if analysis_result:
                    print(f"      AI总结: {analysis_result.get('summary', 'N/A')}")
            else:
                print("    2. 跳过AI分析（未配置API密钥）")
                analysis_result = {
                    'summary': '项目评审会议通知',
                    'importance_score': 8,
                    'events': [{
                        'title': '项目评审会议',
                        'start_time': datetime.now() + timedelta(days=1, hours=15),
                        'end_time': datetime.now() + timedelta(days=1, hours=17),
                        'location': '会议室B201',
                        'importance_level': 'important'
                    }]
                }
            
            # 3. 保存邮件和分析结果
            print("    3. 保存邮件和分析结果...")
            email_id = email_service.save_email_analysis(mock_email, analysis_result)
            if email_id:
                print(f"      邮件保存成功，ID: {email_id}")
            
            # 4. 添加事件到日程
            if analysis_result and analysis_result.get('events'):
                print("    4. 添加事件到日程...")
                for event in analysis_result['events']:
                    event['email_id'] = email_id
                    event_id = scheduler_service.add_event(event)
                    if event_id:
                        print(f"      事件添加成功，ID: {event_id}")
            
            # 5. Notion归档（如果配置了）
            if self.config.notion_config.get('token'):
                print("    5. 归档到Notion...")
                notion_page_id = notion_service.archive_email(mock_email, analysis_result)
                if notion_page_id:
                    print(f"      Notion归档成功，页面ID: {notion_page_id}")
            else:
                print("    5. 跳过Notion归档（未配置Token）")
            
            print("  ✅ 系统集成测试完成")
            return True
            
        except Exception as e:
            print(f"  ❌ 系统集成测试错误: {e}")
            return False
    
    def print_summary(self):
        """打印测试总结"""
        print("\n" + "=" * 50)
        print("📊 测试结果总结")
        print("=" * 50)
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for _, status, _ in self.test_results if status == "PASS")
        failed_tests = sum(1 for _, status, _ in self.test_results if status == "FAIL")
        error_tests = sum(1 for _, status, _ in self.test_results if status == "ERROR")
        
        print(f"总测试数: {total_tests}")
        print(f"通过: {passed_tests} ✅")
        print(f"失败: {failed_tests} ❌")
        print(f"错误: {error_tests} 💥")
        print(f"成功率: {(passed_tests/total_tests*100):.1f}%")
        
        print("\n详细结果:")
        for test_name, status, error in self.test_results:
            status_icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "💥"}[status]
            print(f"  {status_icon} {test_name}: {status}")
            if error:
                print(f"    错误信息: {error}")
        
        if passed_tests == total_tests:
            print("\n🎉 所有测试通过！系统运行正常。")
        else:
            print("\n⚠️  部分测试未通过，请检查配置和依赖。")
        
        print("\n💡 提示:")
        print("  - 如果邮件或AI测试失败，请检查相关配置")
        print("  - 首次运行请先在Web界面完成系统配置")
        print("  - 详细日志请查看 logs/app.log 文件")


def main():
    """主函数"""
    try:
        # 确保必要的目录存在
        Path("data").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        
        # 运行测试
        tester = SystemTester()
        tester.run_all_tests()
        
    except KeyboardInterrupt:
        print("\n\n⏹️  测试被用户中断")
    except Exception as e:
        print(f"\n\n💥 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()