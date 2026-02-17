<template>
  <svg :width="width" :height="height" style="display: block">
    <polyline
      :points="points"
      fill="none"
      :stroke="color"
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>

<script>
export default {
  name: 'Sparkline',
  props: {
    data: {
      type: Array,
      required: true,
      default: () => []
    },
    color: {
      type: String,
      default: '#3b82f6'
    },
    width: {
      type: Number,
      default: 80
    },
    height: {
      type: Number,
      default: 24
    }
  },
  computed: {
    points() {
      if (!this.data || this.data.length === 0) return '';

      const max = Math.max(...this.data);
      const min = Math.min(...this.data);
      const range = max - min || 1;

      return this.data.map((v, i) => {
        const x = (i / (this.data.length - 1)) * this.width;
        const y = this.height - ((v - min) / range) * (this.height - 4) - 2;
        return `${x},${y}`;
      }).join(' ');
    }
  }
};
</script>
