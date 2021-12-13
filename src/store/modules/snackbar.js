const snackbar = {
  namespaced: true,
  state: {
    msg: '',
    visible: false,
    showClose: true,
    timeout: 6000,
    color: 'pink'
  },
  mutations: {
    OPEN_SNACKBAR(state, options) {
      state.msg = options.msg;
      
      state.visible = true;
    },
    CLOSE_SNACKBAR(state) {
      state.visible = false;
    },
    // set snackbar
    setShowClose(state, isShow) {
      state.showClose = isShow;
    },
    setTimeout(state, timeout) {
      state.timeout = timeout;
    },
    setColor(state, color) {
      state.color = color;
    }
  },
  actions: {
    openSnackbar(context, options) {
      let timeout = context.state.timeout;

      context.commit('OPEN_SNACKBAR', {
        msg: options.msg
      });

      setTimeout(() => {
        context.commit('CLOSE_SNACKBAR')
      }, timeout);
    }
  }
}

export default snackbar;