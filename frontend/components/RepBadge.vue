<template>
  <Badge :bg="badgeConfig.bg" :color="badgeConfig.color">{{ rep || 'N/A' }}</Badge>
</template>

<script>
import Badge from './Badge.vue';

export default {
  name: 'RepBadge',
  components: {
    Badge
  },
  props: {
    rep: {
      type: String,
      default: 'N/A'
    }
  },
  computed: {
    badgeConfig() {
      const repMap = {
        HIGH: { bg: '#dcfce7', color: '#166534' },
        MEDIUM: { bg: '#fef3c7', color: '#92400e' },
        LOW: { bg: '#ffedd5', color: '#9a3412' },
        BAD: { bg: '#fee2e2', color: '#991b1b' },
        'N/A': { bg: '#f1f5f9', color: '#64748b' }
      };

      const base = this.rep ? this.rep.replace(/\(.*\)/, '').trim() : 'N/A';
      return repMap[base] || repMap['N/A'];
    }
  }
};
</script>
