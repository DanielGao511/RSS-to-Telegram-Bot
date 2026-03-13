import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from openai import OpenAI
from telethon import functions, types

from . import db, env

logger = logging.getLogger('RSStT.summarizer')

class Summarizer:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.ai_client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def clean_html(self, text: str) -> str:
        """Absolute brute-force cleanup."""
        if "body {" in text or "<style" in text:
            text = re.split(r'body\s*\{|<style', text, flags=re.IGNORECASE)[0]
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'<(style|script|head|body|html|title|div|span|h1|h2|h3)[^>]*>[\s\S]*?</\1>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<(?!/?(b|i|a|blockquote)\b)[^>]+>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\{[\s\S]*?\}', '', text)
        return text.strip()

    async def summarize_channel(self, user: db.User):
        """Summarize updates for a specific channel/user."""
        user_id = user.id
        now = datetime.now(timezone.utc)
        now_beijing = now + timedelta(hours=8)
        date_str = now_beijing.strftime("%Y-%m-%d")
        
        # 1. Triple-Period Naming Logic with Date
        hour = now_beijing.hour
        if 5 <= hour < 12:
            time_tag = "早报"
            emoji = "🌅"
        elif 12 <= hour < 19:
            time_tag = "午报"
            emoji = "🌤️"
        else:
            time_tag = "晚报"
            emoji = "🌙"
        
        try:
            entity = await env.bot.get_entity(user_id)
            channel_name = getattr(entity, 'title', 'RSS')
        except Exception:
            channel_name = "RSS"

        report_title = f"{emoji} <b>{date_str} {channel_name} {time_tag}要闻概览</b>"

        # 2. Dynamic Time Window based on user's interval
        interval_minutes = user.summary_interval or 720
        start_time = now - timedelta(minutes=interval_minutes)
        
        # Get ALL entries in the window (no limit here, but prompt will handle density)
        entries = await db.ChannelEntry.filter(
            user_id=user_id, 
            published_at__gte=start_time
        ).order_by('published_at')

        if not entries:
            return

        entry_count = len(entries)
        logger.info(f"Summarizing {entry_count} entries for {user_id}")
        
        content_to_summarize = ""
        for i, entry in enumerate(entries):
            clean_input = re.sub(r'<[^>]+>', '', entry.content)
            # Send as much as possible, prompt will condense
            content_to_summarize += f"[文章 {i+1}] {entry.title}: {clean_input}\n\n"

        request_id = str(uuid.uuid4())[:8]

        system_prompt = f"你是一个新闻编辑。任务:{request_id}。禁止输出CSS或网页代码。"
        user_prompt = f"""
        分析以下 {entry_count} 篇新闻全文，生成总结报告。

        <b>一句话总结</b>
        <blockquote>[综述核心趋势]</blockquote>

        <b>分类总结</b>
        <b>[分类名]</b>
        <blockquote>[深度归并总结动态，逻辑严密]</blockquote>

        **禁令**：禁止输出 style, body, html 标签。标题必须加粗并独立成行。
        
        数据：
        {content_to_summarize[:110000]}
        """

        try:
            completion = self.ai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            ai_content = self.clean_html(completion.choices[0].message.content)
            
            if "一句话总结" not in ai_content:
                ai_content = f"<b>一句话总结</b>\n{ai_content}"

            final_report = f"{report_title}\n\n{ai_content}\n\n<i>--- 基于 {entry_count} 篇资讯深度总结 ---</i>"
            
            # 3. FIFO Pinning Logic (Max 6 pins)
            msg_ids = user.summary_msg_ids or []
            
            # If we already have 6, unpin the oldest one
            if len(msg_ids) >= 6:
                oldest_msg_id = msg_ids.pop(0)
                try:
                    await env.bot.unpin_message(user_id, oldest_msg_id)
                except Exception:
                    pass

            # Send new summary
            sent_msg = await env.bot.send_message(user_id, final_report, parse_mode='html', link_preview=False)
            
            if sent_msg:
                # Add to queue
                msg_ids.append(sent_msg.id)
                user.summary_msg_ids = msg_ids
                await user.save()
                
                # Pin new summary
                if user.summary_pin:
                    try:
                        await env.bot.pin_message(user_id, sent_msg.id, notify=False)
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"Failed to summarize for {user_id}: {e}")

    async def run_scheduled_summaries(self):
        """Check all users and run summaries."""
        now_utc = datetime.now(timezone.utc)
        now_beijing = now_utc + timedelta(hours=8)
        current_total_minutes = now_beijing.hour * 60 + now_beijing.minute
        
        users = await db.User.filter(summary_enabled=True)

        for user in users:
            # SECONDARY SECURITY CHECK:
            # Ensure the user itself is a MANAGER or the admin of this chat is a MANAGER.
            is_manager_chat = (user.id in env.MANAGER) or (user.admin in env.MANAGER)
            if not is_manager_chat:
                # If it's a channel/group, try to detect admins if not already done
                if user.id < 0 and not user.admin:
                    try:
                        admins = await env.bot.get_participants(user.id, filter=types.ChannelParticipantsAdmins)
                        for admin in admins:
                            if admin.id in env.MANAGER:
                                user.admin = admin.id
                                await user.save(update_fields=['admin'])
                                is_manager_chat = True
                                break
                    except Exception:
                        pass
                
                if not is_manager_chat:
                    continue

            try:
                # Normal schedule check logic
                start_h, start_m = map(int, user.summary_at.split(':'))
                start_total_minutes = start_h * 60 + start_m
                interval = user.summary_interval or 720
                
                diff = current_total_minutes - start_total_minutes
                if diff < 0:
                    diff += 1440
                
                if diff % interval == 0:
                    await self.summarize_channel(user)
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Schedule error for {user.id}: {e}")

    async def cleanup_old_entries(self):
        """Cleanup entries older than 2 days."""
        now = datetime.now(timezone.utc)
        two_days_ago = now - timedelta(days=2)
        await db.ChannelEntry.filter(published_at__lt=two_days_ago).delete()

summarizer_instance: Optional[Summarizer] = None

def get_summarizer() -> Optional[Summarizer]:
    global summarizer_instance
    if summarizer_instance:
        return summarizer_instance
    
    api_key = os.getenv('ALIYUN_API_KEY')
    base_url = os.getenv('ALIYUN_BASE_URL')
    model = os.getenv('MODEL_NAME', 'qwen3.5-flash')

    if api_key and base_url:
        summarizer_instance = Summarizer(api_key, base_url, model)
        return summarizer_instance
    return None

async def run_periodic_summary_task():
    summarizer = get_summarizer()
    if summarizer:
        await summarizer.run_scheduled_summaries()
        if datetime.now().minute == 0:
            await summarizer.cleanup_old_entries()
