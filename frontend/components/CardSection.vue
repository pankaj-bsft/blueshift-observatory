<template>
  <div class="card-section">
    <div v-if="title || hasExtra" class="card-header">
      <h3 class="card-title">{{ title }}</h3>
      <slot name="extra"></slot>
    </div>
    <slot></slot>
  </div>
</template>

<script>
export default {
  name: 'CardSection',
  props: {
    title: {
      type: String,
      default: ''
    }
  },
  computed: {
    hasExtra() {
      return !!this.$slots.extra;
    }
  }
};
</script>

<style scoped>
.card-section {
  background: #fff;
  border-radius: 14px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
  margin-bottom: 24px;
}

.card-header {
  padding: 16px 22px;
  border-bottom: 1px solid #f1f5f9;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  color: #1e293b;
  margin: 0;
}
</style>
