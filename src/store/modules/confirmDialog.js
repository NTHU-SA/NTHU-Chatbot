const confirmDialog = {
  namespaced: true,
  state: {
    title: '',
    msg: '',
    visible: false,
    // delete options
    delItemID: '',
    delApi: '',
    // reset data dispatch
    resetTarget: ''
  },
  mutations: {
    OPEN_DIALOG(state, options) {
      state.title = options.title;
      state.msg = options.msg;

      state.delItemID = options.delItemID;
      state.delApi = options.delApi;
      state.resetTarget = options.resetTarget;

      state.visible = true;
    },
    CLOSE_DIALOG(state) {
      state.visible = false;
    },
  },
  actions: {
    openDialog(context, options) {
      context.commit('OPEN_DIALOG', {
        title: options.title,
        msg: options.msg,
        delItemID: options.delItemID,
        delApi: options.delApi,
        resetTarget: options.resetTarget
      });
    }
  }
}

export default confirmDialog;