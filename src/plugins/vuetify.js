import 'material-design-icons-iconfont/dist/material-design-icons.css'
import '@fortawesome/fontawesome-free/css/all.css'
import Vue from 'vue'
import Vuetify from 'vuetify'

Vue.use(Vuetify)

const opts = {
  icons: {
    iconfont: 'md' || 'mdi' || 'fa',
  },
}

export default new Vuetify(opts)