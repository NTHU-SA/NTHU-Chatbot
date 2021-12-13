import axios from 'axios'

export default {
  namespaced: true,
  state: {
    loading: false,
    data: []
  },
  getters: {
    getFeedbackData(state) {
      return state.data;
    }
  },
  mutations: {
    SET_LOADING(state, isLoading) {
      state.loading = isLoading;
    },
    SET_FEEDBACK_DATA(state, options) {
      state.data = options.data;
    }
  },
  actions: {
    getFeedback(context) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/group/feedback', {
        params: {
          category: 'normal'
        }
      }).then((response) => {
        let res = response.data;

        context.commit('SET_FEEDBACK_DATA', {
          data: res.result
        });

        context.commit('SET_LOADING', false);
      });
    }
  }
}