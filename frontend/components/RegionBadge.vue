<template>
  <span :style="badgeStyle">
    {{ flag }} {{ region }}
  </span>
</template>

<script>
export default {
  name: 'RegionBadge',
  props: {
    region: {
      type: String,
      required: true,
      default: 'US'
    }
  },
  computed: {
    flag() {
      return this.region === 'US' ? '🇺🇸' : '🇪🇺';
    },
    badgeStyle() {
      const isUS = this.region === 'US';
      return {
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        background: isUS ? '#eff6ff' : '#f0fdf4',
        color: isUS ? '#1e40af' : '#166534',
        padding: '3px 10px',
        borderRadius: '6px',
        fontSize: '11px',
        fontWeight: 600
      };
    }
  }
};
</script>
