import asyncio
from episodic_manager import EpisodicMemoryManager


def run_memory_test():
    print("🚀 启动情景记忆双引擎测试...\n")

    # 1. 初始化记忆管理器
    memory_manager = EpisodicMemoryManager()

    # 假设这是我们系统里的一个老用户
    test_user_id = "user_999_vip"

    print("==================================================")
    print("阶段一：模拟前端调用，写入历史评价 (Archiving)")
    print("==================================================")

    # 模拟用户去三亚的差评
    print("📝 正在写入记忆 1...")
    memory_manager.save_experience(
        user_id=test_user_id,
        city="三亚",
        rating=2,
        feedback_text="这次行程安排得太满了，每天跑3个景点，孩子累得直哭。而且海鲜大排档严重宰客，超出了我的预算。"
    )

    # 模拟用户去成都的好评
    print("📝 正在写入记忆 2...")
    memory_manager.save_experience(
        user_id=test_user_id,
        city="成都",
        rating=5,
        feedback_text="非常满意！宽窄巷子的茶馆特别棒，而且每天下午都安排了回酒店午休的时间，节奏很舒服。吃得也很地道。"
    )

    # 模拟另一个用户的评价（用于测试数据隔离）
    print("📝 正在写入干扰记忆 (其他用户的)...")
    memory_manager.save_experience(
        user_id="user_000_other",
        city="北京",
        rating=1,
        feedback_text="北京的烤鸭太难吃了，我再也不吃烤鸭了！"
    )

    print("\n✅ 所有历史记忆已成功双写至 SQLite 和 Qdrant！\n")

    print("==================================================")
    print("阶段二：模拟用户发起新请求，唤醒记忆 (Retrieval)")
    print("==================================================")

    # 假设用户过了半年，想去上海玩，带了孩子
    new_intent = "打算去上海玩3天，带着小朋友，希望能吃点好的。"
    print(f"👤 用户 {test_user_id} 发起新请求：'{new_intent}'")

    print("\n🧠 系统正在 Qdrant 语义空间中进行相似度检索...")
    # 核心测试：传入新需求，看能不能捞出三亚和成都的教训
    recalled_lessons = memory_manager.recall_lessons(
        user_id=test_user_id,
        current_request_text=new_intent,
        k=2  # 捞最相关的2条
    )

    print("\n🎯 唤醒的结果如下：")
    print(recalled_lessons)
    print("\n==================================================")


if __name__ == "__main__":
    run_memory_test()