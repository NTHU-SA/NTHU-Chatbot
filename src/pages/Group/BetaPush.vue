<template>
<div>
  <v-container class="pt-0 pb-8">
    <Chatbot-Push category="beta"></Chatbot-Push>
  </v-container>
</div>
</template>

<script>
import axios from 'axios'

import ChatbotPush from '@/components/_chatbot/Push'

export default {
  components: {
    ChatbotPush
  },
  data() {
    return {}
  },
  mounted() {},
  methods: {
    
  }
}
</script>

<style lang="scss">
</style>

