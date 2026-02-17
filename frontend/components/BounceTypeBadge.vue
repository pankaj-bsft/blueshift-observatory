<template>
  <Badge :bg="badgeConfig.bg" :color="badgeConfig.color">
    {{ type ? type.toUpperCase() : 'N/A' }}
  </Badge>
</template>

<script>
import Badge from './Badge.vue';

export default {
  name: 'BounceTypeBadge',
  components: {
    Badge
  },
  props: {
    type: {
      type: String,
      default: ''
    }
  },
  computed: {
    badgeConfig() {
      const isHard = this.type && this.type.toLowerCase() === 'hard';
      return {
        bg: isHard ? '#fee2e2' : '#fef3c7',
        color: isHard ? '#991b1b' : '#92400e'
      };
    }
  }
};
</script>
