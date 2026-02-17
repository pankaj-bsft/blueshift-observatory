<template>
  <span>{{ displayValue }}</span>
</template>

<script>
export default {
  name: 'AnimNum',
  props: {
    target: {
      type: Number,
      required: true,
      default: 0
    },
    duration: {
      type: Number,
      default: 1000
    }
  },
  data() {
    return {
      displayValue: '0',
      animationFrame: null
    };
  },
  watch: {
    target: {
      immediate: true,
      handler(newTarget) {
        this.animate(newTarget);
      }
    }
  },
  methods: {
    animate(target) {
      if (this.animationFrame) {
        cancelAnimationFrame(this.animationFrame);
      }

      let startTime = null;
      const startValue = 0;

      const step = (timestamp) => {
        if (!startTime) startTime = timestamp;
        const progress = Math.min((timestamp - startTime) / this.duration, 1);
        const currentValue = Math.floor(progress * target);
        this.displayValue = currentValue.toLocaleString();

        if (progress < 1) {
          this.animationFrame = requestAnimationFrame(step);
        }
      };

      this.animationFrame = requestAnimationFrame(step);
    }
  },
  beforeUnmount() {
    if (this.animationFrame) {
      cancelAnimationFrame(this.animationFrame);
    }
  }
};
</script>
