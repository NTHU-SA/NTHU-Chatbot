import Vue from 'vue'
import App from './App.vue'
import router from './router'
import vuetify from './plugins/vuetify'
import store from './store'
// import VueQrcodeReader from 'vue-qrcode-reader'

import 'vuetify/dist/vuetify.min.css'

Vue.config.productionTip = false

Vue.use(vuetify);
// Vue.use(VueQrcodeReader);

new Vue({
  router,
  vuetify,
  store,
  render: h => h(App)
}).$mount('#app')
