import Vue from 'vue'
import Vuex from 'vuex'

import snackbar from './modules/snackbar'
import confirmDialog from './modules/confirmDialog'

// common
import commonNewsWall from '../components/Common/NewsWall/store'
import editNewsDialog from '../components/Common/NewsWall/EditNewsDialog/store'

// user
import userManagement from '../pages/Group/UserManagement/store'
import qaManagement from '../pages/Group/QaManagement/store'
import feedback from '../pages/Group/Feedback/store'
import epidemic from '../components/_chatbot/EpidemicDataController/store'
import epidemicfeedback from '../pages/Group/EpidemicFeedback/store'
import anecdote from '../pages/Group/Anecdote/store'


Vue.use(Vuex)

export default new Vuex.Store({
  modules: {
    snackbar,
    confirmDialog,

    // Common
    commonNewsWall,

    // Group
    userManagement,
    qaManagement,
    feedback,
    epidemic,
    epidemicfeedback,
    anecdote,

    // Instanews
    editNewsDialog
  }
});