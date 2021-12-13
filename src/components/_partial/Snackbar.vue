<template>
<div>
  <v-snackbar bottom v-model="this.$store.state.snackbar.visible">
    <span style="float:left;">{{ this.$store.state.snackbar.msg }}</span>

    <template v-slot:action="{ attrs }">
      <v-btn
        v-if="$store.state.snackbar.showClose" 
        :color="$store.state.snackbar.color"
        text
        v-bind="attrs"
        @click="close"
      >
        Close
      </v-btn>
    </template>
  </v-snackbar>
</div>
</template>

<script>
export default {
  components: {},
  data() {
    return {}
  },
  methods: {
    close() {
       this.$store.commit('snackbar/CLOSE_SNACKBAR')
    }
  },
}
</script>