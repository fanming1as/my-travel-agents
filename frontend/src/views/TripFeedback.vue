<template>
  <div class="feedback-container">
    <button class="trigger-btn" @click="showModal = true">
      ✨ 评价本次旅行计划
    </button>

    <div v-if="showModal" class="modal-overlay" @click.self="showModal = false">
      <div class="modal-content">
        <h3>您对本次【{{ city }}】的规划满意吗？</h3>

        <div class="rating-stars">
          <span
            v-for="star in 5"
            :key="star"
            class="star"
            :class="{ active: star <= rating }"
            @click="rating = star"
          >
            ★
          </span>
        </div>
        <div class="rating-text">{{ rating }} 星</div>

        <textarea
          v-model="feedbackText"
          placeholder="请说说您的真实感受（比如：行程太赶了、某家餐厅不想去...），系统会自动记住您的偏好！"
          rows="4"
        ></textarea>

        <div class="modal-actions">
          <button class="cancel-btn" @click="showModal = false" :disabled="isSubmitting">取消</button>
          <button class="submit-btn" @click="submitFeedback" :disabled="isSubmitting">
            {{ isSubmitting ? '提交中...' : '提交评价' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, defineProps } from 'vue';

// 接收父组件（主页面）传来的数据
const props = defineProps({
  userId: {
    type: String,
    required: true,
    default: 'guest_user'
  },
  city: {
    type: String,
    required: true
  }
});

// 响应式状态
const showModal = ref(false);
const rating = ref(5);           // 默认满分5星
const feedbackText = ref('');
const isSubmitting = ref(false); // 控制防抖和按钮状态

// 核心提交逻辑
const submitFeedback = async () => {
  if (!feedbackText.value.trim()) {
    alert('说点什么吧~');
    return;
  }

  isSubmitting.value = true;

  try {
    // 调用我们后端刚写好的插座 (注意替换为你的真实后端地址)
    const response = await fetch("http://127.0.0.1:8000/api/trip/feedback", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        user_id: props.userId,
        city: props.city,
        rating: rating.value,
        feedback_text: feedbackText.value
      })
    });

    const result = await response.json();

    if (result.status === "success") {
      alert("🎉 感谢评价！系统已永久记住您的旅行偏好。");
      showModal.value = false; // 关闭弹窗
      feedbackText.value = ''; // 清空输入框
    } else {
      alert("提交失败: " + result.message);
    }
  } catch (error) {
    console.error("网络请求报错:", error);
    alert("网络连接失败，请检查后端服务是否启动。");
  } finally {
    isSubmitting.value = false;
  }
};
</script>

<style scoped>
/* 极简美观的 CSS 样式 */
.feedback-container {
  margin-top: 30px;
  text-align: center;
}

.trigger-btn {
  padding: 10px 24px;
  background-color: #4CAF50;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  cursor: pointer;
  transition: background 0.3s;
}
.trigger-btn:hover { background-color: #45a049; }

/* 弹窗遮罩 */
.modal-overlay {
  position: fixed;
  top: 0; left: 0; width: 100vw; height: 100vh;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 1000;
}

/* 弹窗内容 */
.modal-content {
  background: white;
  padding: 24px;
  border-radius: 12px;
  width: 90%;
  max-width: 400px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.rating-stars {
  font-size: 36px;
  color: #e0e0e0;
  cursor: pointer;
  margin: 10px 0;
}
.rating-stars .star.active { color: #FFD700; } /* 金色星星 */
.rating-text { font-size: 14px; color: #666; margin-bottom: 16px; }

textarea {
  width: 100%;
  padding: 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  resize: none;
  font-family: inherit;
  box-sizing: border-box;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 20px;
}

.cancel-btn {
  padding: 8px 16px;
  background: #f1f1f1;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.submit-btn {
  padding: 8px 16px;
  background: #007BFF;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.submit-btn:disabled { background: #99c2ff; cursor: not-allowed; }
</style>