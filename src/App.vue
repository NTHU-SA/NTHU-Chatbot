<template>
  <div id="app">
  <v-app>
    <router-view/>

    <Snackbar style="height:0"></Snackbar>
    <Confirm-Dialog style="height:0"></Confirm-Dialog>
  </v-app>
    
  </div>
</template>

<script>
import '@/assets/scss/app.scss'

import Snackbar from '@/components/_partial/Snackbar'
import ConfirmDialog from '@/components/_partial/ConfirmDialog'

export default {
  components: {
    Snackbar,
    ConfirmDialog
  }
}
</script>
