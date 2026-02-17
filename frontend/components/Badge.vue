<template>
  <span :style="badgeStyle">
    <slot></slot>
  </span>
</template>

<script>
export default {
  name: 'Badge',
  props: {
    bg: {
      type: String,
      default: '#f1f5f9'
    },
    color: {
      type: String,
      default: '#475569'
    }
  },
  computed: {
    badgeStyle() {
      return {
        background: this.bg,
        color: this.color,
        padding: '3px 10px',
        borderRadius: '6px',
        fontSize: '11px',
        fontWeight: 600,
        letterSpacing: '0.02em',
        display: 'inline-block',
        whiteSpace: 'nowrap'
      };
    }
  }
};
</script>
