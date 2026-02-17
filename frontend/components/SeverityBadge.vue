<template>
  <span :style="badgeStyle">{{ severity }}</span>
</template>

<script>
export default {
  name: 'SeverityBadge',
  props: {
    severity: {
      type: String,
      required: true,
      default: 'info'
    }
  },
  computed: {
    badgeStyle() {
      const severityMap = {
        critical: { bg: '#fee2e2', color: '#991b1b', border: '#fecaca' },
        high: { bg: '#ffedd5', color: '#9a3412', border: '#fed7aa' },
        medium: { bg: '#fef3c7', color: '#92400e', border: '#fde68a' },
        info: { bg: '#dbeafe', color: '#1e40af', border: '#bfdbfe' }
      };

      const config = severityMap[this.severity.toLowerCase()] || severityMap.info;

      return {
        padding: '5px 14px',
        borderRadius: '6px',
        fontSize: '11px',
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.5px',
        background: config.bg,
        color: config.color,
        border: `1px solid ${config.border}`,
        whiteSpace: 'nowrap'
      };
    }
  }
};
</script>
