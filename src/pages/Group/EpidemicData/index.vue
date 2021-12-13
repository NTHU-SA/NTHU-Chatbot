<template>
<div>
  <v-container class="pt-0 pb-8">
    <Epidemic-Data-Controller auth="group"></Epidemic-Data-Controller>
  </v-container>
</div>
</template>

<script>
import EpidemicDataController from '@/components/_chatbot/EpidemicDataController'

export default {
  components: {
    EpidemicDataController
  },
  data() {
    return {}
  }
}
</script>