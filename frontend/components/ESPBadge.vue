<template>
  <Badge :bg="badgeConfig.bg" :color="badgeConfig.color">{{ esp }}</Badge>
</template>

<script>
import Badge from './Badge.vue';

export default {
  name: 'ESPBadge',
  components: {
    Badge
  },
  props: {
    esp: {
      type: String,
      required: true,
      default: ''
    }
  },
  computed: {
    badgeConfig() {
      const espMap = {
        sendgrid: {
          bg: 'linear-gradient(135deg, #1A82E2, #1565C0)',
          color: '#fff'
        },
        sparkpost: {
          bg: 'linear-gradient(135deg, #FF6600, #E65100)',
          color: '#fff'
        },
        mailgun: {
          bg: 'linear-gradient(135deg, #D4463B, #B71C1C)',
          color: '#fff'
        }
      };

      const espLower = this.esp.toLowerCase();
      if (espLower.includes('send')) return espMap.sendgrid;
      if (espLower.includes('spark')) return espMap.sparkpost;
      return espMap.mailgun;
    }
  }
};
</script>
