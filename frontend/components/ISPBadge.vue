<template>
  <Badge :bg="badgeConfig.bg" :color="badgeConfig.color">{{ isp }}</Badge>
</template>

<script>
import Badge from './Badge.vue';

export default {
  name: 'ISPBadge',
  components: {
    Badge
  },
  props: {
    isp: {
      type: String,
      required: true,
      default: 'Other'
    }
  },
  computed: {
    badgeConfig() {
      const ispMap = {
        gmail: { bg: '#dbeafe', color: '#1e40af' },
        apple: { bg: '#f1f5f9', color: '#334155' },
        comcast: { bg: '#ede9fe', color: '#5b21b6' },
        yahoo: { bg: '#ede9fe', color: '#6d28d9' },
        outlook: { bg: '#dbeafe', color: '#1e40af' },
        other: { bg: '#f1f5f9', color: '#64748b' }
      };

      const key = this.isp ? this.isp.toLowerCase() : 'other';
      return ispMap[key] || ispMap.other;
    }
  }
};
</script>
