"""Deterministic routing for offline demos; live routing uses the model contract.

仅供 MockModelClient 使用。关键词的判断顺序会影响演示分支，
但这里不是线上意图分类器；真实模型的路由规则在 prompts.py。
"""

import re


def demo_route(message, has_report=False):
    """按有限关键词返回离线演示意图；真实模式由模型判断。"""
    text = message.lower()
    # 优先识别明确的“只倾听/不分析”，再判断专门工作流，减少演示误触发。
    if any(
        x in text
        for x in (
            "只想聊",
            "只想倾诉",
            "听我说",
            "不想分析",
            "不要分析",
            "just listen",
            "don't analyze",
        )
    ):
        kind = "chat"
    elif any(x in text for x in ("不要搜索", "不用查", "don't search", "no search")):
        kind = "knowledge"
    elif any(
        x in text
        for x in (
            "论文",
            "文献",
            "出处",
            "证据来源",
            "研究支持",
            "相关证据",
            "来源",
            "谷歌学术",
            "scholar",
            "openalex",
            "papers",
            "evidence",
            "sources",
            "citations",
        )
    ):
        kind = "evidence"
    elif any(
        x in text
        for x in (
            "演练",
            "模拟对话",
            "练习对话",
            "rehearse",
            "role-play",
            "roleplay",
            "practise a conversation",
            "practice a conversation",
        )
    ) and not any(
        x in text
        for x in (
            "不要演练",
            "不用演练",
            "不要模拟",
            "不想演练",
            "不需要演练",
            "don't rehearse",
            "no roleplay",
            "do not rehearse",
        )
    ):
        kind = "rehearsal"
    # 更新判断需要旧报告作为对象；没有旧报告时不能凭空修正假设状态。
    elif has_report and any(
        x in text
        for x in (
            "更新判断",
            "反馈",
            "update the report",
            "revise the report",
        )
    ):
        kind = "feedback"
    elif any(
        x in text
        for x in (
            "详细分析",
            "分析报告",
            "逐项比较",
            "detailed analysis",
            "analysis report",
            "structured analysis",
            "compare options",
        )
    ):
        kind = "analysis"
    elif any(
        x in text for x in ("他这个举动", "她这个举动", "他的话", "她的话", "这句话")
    ):
        kind = "chat"
    elif re.search(
        r"什么是|是什么意思|解释.{0,20}(概念|依恋|博弈)|科普|资料|理论|what is|what are|explain|define",
        text,
    ):
        kind = "knowledge"
    else:
        kind = "chat"
    return {"intent": kind, "reason": "Offline demo intent rule"}
