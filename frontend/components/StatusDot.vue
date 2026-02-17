<template>
  <span :style="dotStyle"></span>
</template>

<script>
export default {
  name: 'StatusDot',
  props: {
    status: {
      type: String,
      required: true,
      default: 'healthy'
    }
  },
  computed: {
    dotStyle() {
      const colorMap = {
        healthy: '#10b981',
        warning: '#f59e0b',
        low: '#f97316',
        critical: '#ef4444'
      };

      const color = colorMap[this.status] || '#94a3b8';

      return {
        display: 'inline-block',
        width: '8px',
        height: '8px',
        borderRadius: '50%',
        background: color,
        boxShadow: `0 0 6px ${color}40`
      };
    }
  }
};
</script>
