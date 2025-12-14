# -*- coding: utf-8 -*-
"""
AI服务模块 - 集成大模型API进行邮件内容分析
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import requests
from dateutil import parser as date_parser

from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class AIService:
    """AI服务类"""
    
    def __init__(self, config: Config):
        """初始化AI服务
        
        Args:
            config: 配置对象
        """
        self.config = config
        # 使用完整配置获取真实的敏感信息
        full_config = config.get_full_config()
        self.ai_config = full_config.get('ai', {})
        self.keywords_config = config.get_keywords()
        
        # 设置API参数
        self.provider = self.ai_config.get('provider', 'openai')
        self.api_key = self.ai_config.get('api_key', '')
        self.model = self.ai_config.get('model', 'gpt-3.5-turbo')
        self.base_url = self.ai_config.get('base_url', '')
        self.max_tokens = self.ai_config.get('max_tokens', 1000)
        self.temperature = self.ai_config.get('temperature', 0.3)
    
    def _prepare_analysis_prompt(self, subject: str, content: str, keywords_config: Dict[str, List[str]] = None, reference_time: datetime = None) -> str:
        """准备分析提示词
        
        Args:
            subject: 邮件主题
            content: 邮件内容
            keywords_config: 关键词配置
        
        Returns:
            分析提示词
        """
        if keywords_config is None:
            keywords_config = self.keywords_config
        # 获取当前时间作为上下文（可用邮件接收时间覆盖，避免“今天/明天”误判）
        current_time = None
        if reference_time:
            try:
                if isinstance(reference_time, str):
                    # 兼容 ISO 字符串
                    reference_time = datetime.fromisoformat(reference_time.replace('Z', '+00:00'))
                current_time = reference_time
            except Exception:
                current_time = None
        if current_time is None:
            current_time = datetime.now()
        current_date_str = current_time.strftime('%Y-%m-%d')
        current_datetime_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
        
        prompt = f"""
你是一个专业的邮件分析助手，需要分析邮件内容并提取关键信息。

当前时间：{current_datetime_str}
当前日期：{current_date_str}

请分析以下邮件内容：

主题：{subject}
内容：{content}

请按照以下JSON格式返回分析结果：

{{
    "summary": "一句话总结邮件内容",
    "importance_score": 评分1-10（10最重要），
    "importance_reason": "重要性评价理由",
    "events": [
        {{
            "title": "事件标题",
            "description": "事件描述",
            "start_time": "2025-09-14 14:00:00",
            "end_time": "2025-09-14 16:00:00（可选）",
            "location": "地点（可选）",
            "importance_level": "important/normal/unimportant",
            "duration_type": "point/duration/deadline"
        }}
    ]
}}

分析要求：
1. 仔细识别邮件中的时间信息，包括：
   - 具体日期时间（如：2024年1月15日下午2点）
   - 相对时间（如：明天、下周一）
   - 截止时间（如：作业提交截止时间）
   - 持续时间（如：会议从2点到4点）

2. 重要性评级标准：
   - important (8-10分)：考试、作业截止、重要会议、紧急事务
   - normal (4-7分)：一般会议、通知、日常安排
   - unimportant (1-3分)：讲座报名、非必要活动、广告信息

3. 如果邮件中有多个时间节点，请为每个时间节点创建单独的事件

4. 如果没有明确的时间信息，events数组可以为空

5. **重要**：时间格式必须使用具体的日期时间，不要使用YYYY-MM-DD这样的占位符格式！
   - 正确示例："2025-09-14 14:00:00"
   - 错误示例："YYYY-MM-DD HH:MM:SS"
   - 如果邮件中提到"明天下午2点"，请根据当前时间计算出具体日期

6. 相对时间转换：
   - "明天" = {(current_time + timedelta(days=1)).strftime('%Y-%m-%d')}
   - "后天" = {(current_time + timedelta(days=2)).strftime('%Y-%m-%d')}
   - "下周一" = 请计算具体日期

6. duration_type说明：
   - point: 时间点事件（如：会议开始时间）
   - duration: 持续时间事件（有开始和结束时间）
   - deadline: 截止时间事件（如：作业提交截止）

请只返回JSON格式的结果，不要包含其他文字说明。
        """
        
        return prompt.strip()
    
    def _call_ai_api(self, prompt: str) -> Dict[str, Any]:
        """调用AI API（支持多个提供商）
        
        Args:
            prompt: 提示词
        
        Returns:
            API响应结果
        """
        if self.provider.lower() == 'openai':
            return self._call_openai_api(prompt)
        elif self.provider.lower() == 'claude':
            return self._call_claude_api(prompt)
        elif self.provider.lower() == 'local':
            return self._call_local_api(prompt)
        elif self.provider.lower() == 'custom':
            return self._call_openai_api(prompt)  # 自定义提供商使用OpenAI兼容格式
        else:
            return {
                'success': False,
                'error': f'不支持的AI提供商: {self.provider}'
            }
    
    def _call_openai_api(self, prompt: str) -> Dict[str, Any]:
        """调用OpenAI API
        
        Args:
            prompt: 提示词
        
        Returns:
            API响应结果
        """
        try:
            # 设置API URL
            if self.base_url:
                clean_base_url = self.base_url.strip().rstrip('/')
                
                # 特殊处理aihubmix
                if 'aihubmix.com' in clean_base_url:
                    # aihubmix需要/v1前缀
                    if '/chat/completions' not in clean_base_url:
                        if '/v1' not in clean_base_url:
                            api_url = f"{clean_base_url}/v1/chat/completions"
                        else:
                            api_url = f"{clean_base_url}/chat/completions"
                    else:
                        api_url = clean_base_url
                else:
                    # 标准OpenAI API格式
                    if '/chat/completions' in clean_base_url:
                        api_url = clean_base_url
                    elif clean_base_url.endswith('/v1'):
                        api_url = f"{clean_base_url}/chat/completions"
                    else:
                        api_url = f"{clean_base_url}/v1/chat/completions"
            else:
                api_url = "https://api.openai.com/v1/chat/completions"
            
            # 准备请求头
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            # 准备请求数据
            data = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }
            
            # 添加可选参数
            if self.max_tokens and self.max_tokens > 0:
                # 根据API服务商选择合适的参数名
                if 'aihubmix.com' in api_url:
                    data["max_completion_tokens"] = self.max_tokens
                else:
                    data["max_tokens"] = self.max_tokens
            
            if self.temperature is not None:
                data["temperature"] = self.temperature
            
            # 对于某些自定义API，可能需要stream参数
            data["stream"] = False
            
            # 记录简化且脱敏的请求信息
            logger.info("=== AI API请求开始 ===")
            logger.info(f"请求URL: {api_url}")
            logger.info("请求方法: POST")
            # 脱敏头信息，不打印Authorization
            safe_headers = {k: ('***' if k.lower() == 'authorization' else v) for k, v in headers.items()}
            logger.debug(f"请求头(已脱敏): {json.dumps(safe_headers, ensure_ascii=False)}")
            # 不打印完整请求体与提示词，仅输出长度与关键元信息
            logger.info(f"请求体大小: {len(json.dumps(data, ensure_ascii=False))} 字符; 提示词长度: {len(prompt)} 字符")
            logger.info(f"使用模型: {self.model}")
            
            # 发送请求
            response = requests.post(
                api_url,
                headers=headers,
                json=data,
                timeout=30
            )
            
            # 记录响应元信息（不打印正文）
            response_text = response.text
            logger.info("=== AI API响应信息 ===")
            logger.info(f"响应状态码: {response.status_code}")
            logger.debug(f"响应头: {dict(response.headers)}")
            logger.info(f"响应体大小: {len(response_text)} 字符; 内容类型: {response.headers.get('content-type', '未知')}")
            if not response_text:
                logger.error("响应内容为空！")
            
            if response.status_code != 200:
                logger.error(f"HTTP错误 {response.status_code}")
                # 不打印完整错误响应以避免泄露
                logger.error("HTTP错误，已省略响应正文以保护隐私")
            
            response.raise_for_status()
            
            if not response_text or not response_text.strip():
                logger.error("API返回空响应")
                return {
                    'success': False,
                    'error': 'API返回空响应'
                }
            
            try:
                result = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"响应JSON解析失败: {e}")
                # 不打印原始响应以避免泄露
                logger.error("响应JSON解析失败，正文已省略")
                return {
                    'success': False,
                    'error': f'响应JSON解析失败: {str(e)}'
                }
            
            if 'choices' in result and len(result['choices']) > 0:
                choice = result['choices'][0]
                content = choice['message']['content']
                finish_reason = choice.get('finish_reason', 'unknown')
                
                # 记录完成原因
                logger.info(f"AI响应完成原因: {finish_reason}")
                
                # 检查内容是否为空
                if not content or not content.strip():
                    if finish_reason == 'length':
                        logger.error("AI API因长度限制返回空内容，需要减少输入长度")
                        return {
                            'success': False,
                            'error': 'AI响应因长度限制被截断，请减少邮件内容长度或增加max_tokens设置'
                        }
                    else:
                        logger.error(f"AI API返回空内容，完成原因: {finish_reason}")
                        return {
                            'success': False,
                            'error': f'AI API返回空内容 (原因: {finish_reason})'
                        }
                
                return {
                    'success': True,
                    'content': content,
                    'model': self.model
                }
            else:
                return {
                    'success': False,
                    'error': '无效的API响应格式'
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenAI API请求失败: {e}")
            
            # 如果是400错误，尝试使用简化的请求格式
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 400:
                logger.info("尝试使用简化的请求格式重新发送请求")
                try:
                    # 简化的请求数据，只包含必要参数
                    simple_data = {
                        "model": self.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ]
                    }
                    
                    # 尝试添加max_completion_tokens
                    if self.max_tokens and self.max_tokens > 0:
                        simple_data["max_completion_tokens"] = self.max_tokens
                    
                    logger.info(f"简化请求数据: {json.dumps(simple_data, ensure_ascii=False)}")
                    
                    response = requests.post(
                        api_url,
                        headers=headers,
                        json=simple_data,
                        timeout=30
                    )
                    
                    logger.info(f"简化请求响应状态码: {response.status_code}")
                    if response.status_code != 200:
                        logger.error(f"简化请求响应内容: {response.text}")
                    
                    response.raise_for_status()
                    
                    result = response.json()
                    
                    if 'choices' in result and len(result['choices']) > 0:
                        content = result['choices'][0]['message']['content']
                        return {
                            'success': True,
                            'content': content,
                            'model': self.model
                        }
                    else:
                        return {
                            'success': False,
                            'error': '无效的API响应格式'
                        }
                        
                except requests.exceptions.RequestException as retry_e:
                    logger.error(f"简化请求也失败: {retry_e}")
                    return {
                        'success': False,
                        'error': f'API请求失败（已尝试简化格式）: {str(retry_e)}'
                    }
            
            return {
                'success': False,
                'error': f'API请求失败: {str(e)}'
            }
        except Exception as e:
            logger.error(f"调用OpenAI API时出错: {e}")
            return {
                'success': False,
                'error': f'API调用错误: {str(e)}'
            }
    
    def _call_claude_api(self, prompt: str) -> Dict[str, Any]:
        """调用Claude API
        
        Args:
            prompt: 提示词
        
        Returns:
            API响应结果
        """
        try:
            # 设置API URL
            if self.base_url:
                api_url = f"{self.base_url.rstrip('/')}/v1/messages"
            else:
                api_url = "https://api.anthropic.com/v1/messages"
            
            # 准备请求头
            headers = {
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            }
            
            # 准备请求数据
            data = {
                "model": self.model or "claude-3-sonnet-20240229",
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }
            
            # 发送请求
            response = requests.post(
                api_url,
                headers=headers,
                json=data,
                timeout=30
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            if 'content' in result and len(result['content']) > 0:
                content = result['content'][0]['text']
                return {
                    'success': True,
                    'content': content,
                    'model': self.model
                }
            else:
                return {
                    'success': False,
                    'error': '无效的API响应格式'
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Claude API请求失败: {e}")
            return {
                'success': False,
                'error': f'API请求失败: {str(e)}'
            }
        except Exception as e:
            logger.error(f"调用Claude API时出错: {e}")
            return {
                'success': False,
                'error': f'API调用错误: {str(e)}'
            }
    
    def _call_local_api(self, prompt: str) -> Dict[str, Any]:
        """调用本地模型API
        
        Args:
            prompt: 提示词
        
        Returns:
            API响应结果
        """
        try:
            # 设置API URL（通常是Ollama或其他本地服务）
            if self.base_url:
                api_url = f"{self.base_url.rstrip('/')}/api/generate"
            else:
                api_url = "http://localhost:11434/api/generate"
            
            # 准备请求头
            headers = {
                "Content-Type": "application/json"
            }
            
            # 准备请求数据（Ollama格式）
            data = {
                "model": self.model or "llama2",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens
                }
            }
            
            # 发送请求
            response = requests.post(
                api_url,
                headers=headers,
                json=data,
                timeout=60  # 本地模型可能需要更长时间
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            if 'response' in result:
                content = result['response']
                return {
                    'success': True,
                    'content': content,
                    'model': self.model
                }
            else:
                return {
                    'success': False,
                    'error': '无效的API响应格式'
                }
                
        except requests.exceptions.RequestException as e:
            logger.error(f"本地模型API请求失败: {e}")
            return {
                'success': False,
                'error': f'API请求失败: {str(e)}'
            }
        except Exception as e:
            logger.error(f"调用本地模型API时出错: {e}")
            return {
                'success': False,
                'error': f'API调用错误: {str(e)}'
            }
    
    def _parse_ai_response(self, response_content: str) -> Dict[str, Any]:
        """解析AI响应内容
        
        Args:
            response_content: AI返回的内容
        
        Returns:
            解析后的结构化数据
        """
        try:
            # 尝试提取JSON内容
            json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                result = json.loads(json_str)
            else:
                # 如果没有找到JSON，尝试直接解析
                result = json.loads(response_content)
            
            # 验证和标准化结果
            standardized_result = {
                'summary': result.get('summary', ''),
                'importance_score': max(1, min(10, int(result.get('importance_score', 5)))),
                'importance_reason': result.get('importance_reason', ''),
                'events': []
            }
            
            # 处理事件列表
            events = result.get('events', [])
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict):
                        processed_event = self._process_event_data(event)
                        if processed_event:
                            standardized_result['events'].append(processed_event)
            
            return standardized_result
            
        except json.JSONDecodeError as e:
            logger.error(f"解析AI响应JSON失败: {e}")
            logger.error(f"响应内容: {response_content}")
            
            # 尝试从响应中提取JSON部分
            cleaned_content = self._extract_json_from_response(response_content)
            if cleaned_content:
                try:
                    result = json.loads(cleaned_content)
                    logger.info("成功从清理后的内容中解析JSON")
                    
                    # 标准化结果格式
                    standardized_result = {
                        'summary': result.get('summary', '邮件内容摘要'),
                        'importance_score': result.get('importance_score', 5),
                        'importance_reason': result.get('importance_reason', ''),
                        'events': []
                    }
                    
                    # 处理事件列表
                    events = result.get('events', [])
                    if isinstance(events, list):
                        for event in events:
                            if isinstance(event, dict):
                                processed_event = self._process_event_data(event)
                                if processed_event:
                                    standardized_result['events'].append(processed_event)
                    
                    return standardized_result
                    
                except json.JSONDecodeError:
                    logger.error("清理后的内容仍无法解析为JSON")
            
            # 返回默认结果
            return {
                'summary': '邮件内容分析失败',
                'importance_score': 5,
                'importance_reason': 'AI响应解析失败',
                'events': []
            }
        except Exception as e:
            logger.error(f"处理AI响应时出错: {e}")
            return {
                'summary': '邮件内容分析出错',
                'importance_score': 5,
                'importance_reason': f'处理错误: {str(e)}',
                'events': []
            }
    
    def _process_event_data(self, event_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理事件数据
        
        Args:
            event_data: 原始事件数据
        
        Returns:
            处理后的事件数据
        """
        try:
            # 解析开始时间
            start_time_str = event_data.get('start_time', '')
            if not start_time_str:
                logger.warning("事件缺少开始时间，跳过")
                return None
            
            start_time = self._parse_datetime(start_time_str)
            if not start_time:
                logger.warning(f"无法解析开始时间: {start_time_str}")
                return None
            
            # 解析结束时间（可选）
            end_time = None
            end_time_str = event_data.get('end_time', '')
            if end_time_str:
                end_time = self._parse_datetime(end_time_str)
            
            # 确定重要性级别
            importance_level = event_data.get('importance_level', 'normal')
            if importance_level not in ['important', 'normal', 'unimportant']:
                importance_level = 'normal'
            
            # 根据重要性设置颜色
            reminder_config = self.config.reminder_config
            colors = reminder_config.get('colors', {})
            color = colors.get(importance_level, '#4444FF')
            
            processed_event = {
                'title': event_data.get('title', '未命名事件'),
                'description': event_data.get('description', ''),
                'start_time': start_time.isoformat() if start_time else None,
                'end_time': end_time.isoformat() if end_time else None,
                'location': event_data.get('location', ''),
                'importance_level': importance_level,
                'color': color,
                'duration_type': event_data.get('duration_type', 'point'),
                'reminder_times': [rt.isoformat() for rt in self._calculate_reminder_times(start_time, importance_level)]
            }
            
            return processed_event
            
        except Exception as e:
            logger.error(f"处理事件数据失败: {e}")
            return None
    
    def _parse_datetime(self, datetime_str: str) -> Optional[datetime]:
        """解析日期时间字符串
        
        Args:
            datetime_str: 日期时间字符串
        
        Returns:
            解析后的datetime对象
        """
        try:
            # 检查是否是占位符格式
            if datetime_str in ['YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD', 'YYYY-MM-DD 00:00:00', 'YYYY-MM-DD 14:00:00']:
                logger.warning(f"AI返回了占位符时间格式: {datetime_str}，跳过此事件")
                return None
            
            # 尝试标准格式解析
            formats = [
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d %H:%M',
                '%Y-%m-%d',
                '%Y/%m/%d %H:%M:%S',
                '%Y/%m/%d %H:%M',
                '%Y/%m/%d'
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(datetime_str, fmt)
                except ValueError:
                    continue
            
            # 尝试使用dateutil解析
            return date_parser.parse(datetime_str)
            
        except Exception as e:
            logger.warning(f"解析日期时间失败: {datetime_str}, 错误: {e}")
            return None
    
    def _extract_json_from_response(self, response_content: str) -> Optional[str]:
        """从AI响应中提取JSON部分
        
        Args:
            response_content: AI响应内容
        
        Returns:
            提取的JSON字符串，如果没有找到则返回None
        """
        if not response_content or not response_content.strip():
            return None
        
        try:
            # 移除前后空白字符
            content = response_content.strip()
            
            # 尝试找到JSON对象的开始和结束
            start_idx = content.find('{')
            if start_idx == -1:
                return None
            
            # 从后往前找最后一个}
            end_idx = content.rfind('}')
            if end_idx == -1 or end_idx <= start_idx:
                return None
            
            # 提取JSON部分
            json_part = content[start_idx:end_idx + 1]
            
            # 尝试解析以验证是否为有效JSON
            json.loads(json_part)
            return json_part
            
        except (json.JSONDecodeError, ValueError, IndexError):
            # 如果提取失败，尝试其他方法
            try:
                # 使用正则表达式匹配JSON对象
                import re
                json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                matches = re.findall(json_pattern, content, re.DOTALL)
                
                for match in matches:
                    try:
                        json.loads(match)
                        return match
                    except json.JSONDecodeError:
                        continue
                        
            except Exception:
                pass
        
        return None
    
    def _calculate_reminder_times(self, event_time: datetime, importance_level: str) -> List[datetime]:
        """计算提醒时间
        
        Args:
            event_time: 事件时间
            importance_level: 重要性级别
        
        Returns:
            提醒时间列表
        """
        reminder_times = []
        current_time = datetime.now()
        
        if importance_level == 'important':
            # 重要事件：提前3天、1天、3小时、1小时提醒
            reminder_offsets = [
                timedelta(days=3),
                timedelta(days=1),
                timedelta(hours=3),
                timedelta(hours=1)
            ]
            
            for offset in reminder_offsets:
                reminder_time = event_time - offset
                if reminder_time > current_time:
                    reminder_times.append(reminder_time)
        
        elif importance_level == 'normal':
            # 普通事件：提前1天、3小时提醒
            reminder_offsets = [
                timedelta(days=1),
                timedelta(hours=3)
            ]
            
            for offset in reminder_offsets:
                reminder_time = event_time - offset
                if reminder_time > current_time:
                    reminder_times.append(reminder_time)
        
        elif importance_level == 'unimportant':
            # 不重要事件：不设置提醒
            pass
        
        return sorted(reminder_times)
    
    def analyze_email_content(self, content: str, subject: str = '', max_retries: int = 2, user_id: int = None, reference_time: datetime = None) -> Dict[str, Any]:
        """分析邮件内容
        
        Args:
            content: 邮件内容
            subject: 邮件主题
            max_retries: 最大重试次数
            user_id: 用户ID
        
        Returns:
            分析结果
        """
        try:
            logger.info(f"开始分析邮件: {subject}")
            
            # 记录AI请求
            self._record_ai_request('email_analysis', user_id)
            
            # 如果提供了用户ID，获取用户的AI配置和关键词配置
            if user_id:
                from ..services.config_service import UserConfigService
                config_service = UserConfigService()
                user_ai_config = config_service.get_ai_config(user_id)
                user_keywords_config = config_service.get_keywords_config(user_id)
                
                # 使用用户配置覆盖默认配置（仅当值为非空且非占位符时覆盖）
                def _pick(value, fallback):
                    if value is None:
                        return fallback
                    if isinstance(value, str):
                        v = value.strip()
                        if v == '' or v == '***':
                            return fallback
                        return value
                    return value

                api_key = _pick(user_ai_config.get('api_key'), self.api_key)
                provider = _pick(user_ai_config.get('provider'), self.provider)
                model = _pick(user_ai_config.get('model'), self.model)
                base_url = _pick(user_ai_config.get('base_url'), self.base_url)
                max_tokens = _pick(user_ai_config.get('max_tokens'), self.max_tokens)
                temperature = _pick(user_ai_config.get('temperature'), self.temperature)
                keywords_config = user_keywords_config
            else:
                # 使用默认配置
                api_key = self.api_key
                provider = self.provider
                model = self.model
                base_url = self.base_url
                max_tokens = self.max_tokens
                temperature = self.temperature
                keywords_config = self.keywords_config
            
            # 检查配置
            if not api_key:
                logger.error("AI API密钥未配置")
                return {
                    'summary': '未配置AI服务',
                    'importance_score': 5,
                    'importance_reason': 'AI API密钥未配置',
                    'events': [],
                    'ai_model': 'none'
                }
            
            original_content = content
            
            for attempt in range(max_retries + 1):
                # 准备提示词
                prompt = self._prepare_analysis_prompt(subject, content, keywords_config, reference_time)
                
                # 临时设置配置参数
                original_api_key = self.api_key
                original_provider = self.provider
                original_model = self.model
                original_base_url = self.base_url
                original_max_tokens = self.max_tokens
                original_temperature = self.temperature
                
                self.api_key = api_key
                self.provider = provider
                self.model = model
                self.base_url = base_url
                self.max_tokens = max_tokens
                self.temperature = temperature
                
                try:
                    # 调用AI API
                    api_result = self._call_ai_api(prompt)
                finally:
                    # 恢复原始配置
                    self.api_key = original_api_key
                    self.provider = original_provider
                    self.model = original_model
                    self.base_url = original_base_url
                    self.max_tokens = original_max_tokens
                    self.temperature = original_temperature
                
                if not api_result['success']:
                    error_msg = api_result['error']
                    
                    # 检查是否是长度限制问题
                    if 'length' in error_msg.lower() and attempt < max_retries:
                        # 缩短邮件内容重试
                        content_length = len(content)
                        new_length = int(content_length * 0.7)  # 减少30%
                        content = content[:new_length] + "\n\n[邮件内容已截断]" if new_length < content_length else content
                        
                        logger.warning(f"因长度限制重试分析，内容从 {content_length} 字符缩短到 {len(content)} 字符")
                        continue
                    else:
                        logger.error(f"AI API调用失败: {error_msg}")
                        return {
                            'summary': 'AI分析失败',
                            'importance_score': 5,
                            'importance_reason': error_msg,
                            'events': [],
                            'ai_model': model
                        }
                
                # 解析AI响应
                analysis_result = self._parse_ai_response(api_result['content'])
                analysis_result['ai_model'] = api_result['model']
                
                if analysis_result:
                    events_count = len(analysis_result['events'])
                    if attempt > 0:
                        logger.info(f"邮件分析完成（第{attempt+1}次尝试）: {subject}, 提取到 {events_count} 个事件")
                    else:
                        logger.info(f"邮件分析完成: {subject}, 提取到 {events_count} 个事件")
                    return analysis_result
                else:
                    if attempt < max_retries:
                        logger.warning(f"分析结果解析失败，准备重试 (尝试 {attempt + 1}/{max_retries + 1})")
                        continue
            
            logger.error(f"邮件分析失败，已尝试 {max_retries + 1} 次")
            return {
                'summary': '邮件分析失败',
                'importance_score': 5,
                'importance_reason': f'已尝试 {max_retries + 1} 次仍然失败',
                'events': [],
                'ai_model': self.model
            }
            
        except Exception as e:
            logger.error(f"分析邮件内容时出错: {e}")
            return {
                'summary': '邮件分析出错',
                'importance_score': 5,
                'importance_reason': f'分析过程出错: {str(e)}',
                'events': [],
                'ai_model': self.model
            }
    
    def test_connection(self, user_id: int = None) -> Dict[str, Any]:
        """测试AI服务连接
        
        Args:
            user_id: 用户ID
        
        Returns:
            测试结果
        """
        try:
            test_content = "这是一个测试邮件，明天下午2点有一个重要的会议。"
            test_subject = "测试邮件"
            
            # 记录AI请求
            self._record_ai_request('connection_test', user_id)
            
            result = self.analyze_email_content(test_content, test_subject, user_id=user_id)
            
            return {
                'success': True,
                'message': 'AI服务连接正常',
                'test_result': result
            }
            
        except Exception as e:
            logger.error(f"AI服务连接测试失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_supported_models(self) -> Dict[str, List[str]]:
        """获取支持的模型列表
        
        Returns:
            按提供商分组的模型列表
        """
        models = {
            'openai': [
                'gpt-3.5-turbo',
                'gpt-3.5-turbo-16k',
                'gpt-4',
                'gpt-4-turbo-preview',
                'gpt-4-vision-preview',
                'gpt-4o',
                'gpt-4o-mini'
            ],
            'claude': [
                'claude-3-haiku-20240307',
                'claude-3-sonnet-20240229',
                'claude-3-opus-20240229',
                'claude-3-5-sonnet-20241022'
            ],
            'local': [
                'llama2',
                'llama2:13b',
                'llama2:70b',
                'codellama',
                'mistral',
                'mixtral',
                'qwen',
                'gemma'
            ]
        }
        
        return models
    
    def get_provider_info(self) -> Dict[str, Dict[str, Any]]:
        """获取AI提供商信息
        
        Returns:
            提供商信息字典
        """
        providers = {
            'openai': {
                'name': 'OpenAI',
                'description': '',
                'api_key_required': True,
                'base_url_optional': False,
                'default_model': 'gpt-3.5-turbo',
                'pricing': '',
                'features': []
            },
            'claude': {
                'name': 'Anthropic Claude',
                'description': '',
                'api_key_required': True,
                'base_url_optional': False,
                'default_model': 'claude-3-sonnet-20240229',
                'pricing': '',
                'features': []
            },
            'local': {
                'name': '本地模型',
                'description': '本地部署的开源模型（如Ollama），完全私有',
                'api_key_required': False,
                'base_url_optional': True,
                'default_model': 'llama2',
                'pricing': '免费使用',
                'features': ['完全私有', '无网络依赖', '可自定义']
            },
            'custom': {
                'name': '自定义',
                'description': '自定义AI服务提供商，支持任何兼容OpenAI API的服务',
                'api_key_required': True,
                'base_url_optional': True,
                'default_model': '',
                'pricing': '根据提供商而定',
                'features': ['完全自定义', '灵活配置', '兼容性强']
            }
        }
        
        return providers
    
    def _record_ai_request(self, request_type: str, user_id: int = None):
        """记录AI请求
        
        Args:
            request_type: 请求类型
            user_id: 用户ID
        """
        try:
            # 这里可以添加AI使用统计逻辑
            # 例如记录到数据库或日志文件
            logger.info(f"AI请求记录 - 类型: {request_type}, 用户ID: {user_id}, 时间: {datetime.now()}")
        except Exception as e:
            logger.error(f"记录AI请求失败: {e}")