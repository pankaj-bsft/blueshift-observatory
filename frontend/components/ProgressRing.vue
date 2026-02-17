<template>
  <svg :width="size" :height="size" style="transform: rotate(-90deg)">
    <circle
      :cx="size/2"
      :cy="size/2"
      :r="radius"
      fill="none"
      stroke="#e5e7eb"
      :stroke-width="stroke"
    />
    <circle
      :cx="size/2"
      :cy="size/2"
      :r="radius"
      fill="none"
      :stroke="ringColor"
      :stroke-width="stroke"
      :stroke-dasharray="circumference"
      :stroke-dashoffset="offset"
      stroke-linecap="round"
      style="transition: stroke-dashoffset 0.8s ease"
    />
  </svg>
</template>

<script>
export default {
  name: 'ProgressRing',
  props: {
    value: {
      type: Number,
      required: true,
      default: 0
    },
    size: {
      type: Number,
      default: 30
    },
    stroke: {
      type: Number,
      default: 3
    }
  },
  computed: {
    radius() {
      return (this.size - this.stroke) / 2;
    },
    circumference() {
      return 2 * Math.PI * this.radius;
    },
    offset() {
      return this.circumference - (this.value / 100) * this.circumference;
    },
    ringColor() {
      if (this.value >= 95) return '#10b981';
      if (this.value >= 85) return '#f59e0b';
      return '#ef4444';
    }
  }
};
</script>
