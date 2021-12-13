import axios from 'axios'

export default {
  namespaced: true,
  state: {
    loading: false,
    data: []
  },
  getters: {
    getAnecdoteData(state) {
      return state.data;
    }
  },
  mutations: {
    SET_LOADING(state, isLoading) {
      state.loading = isLoading;
    },
    SET_ANECDOTE_DATA(state, options) {
      state.data = options.data;
    }
  },
  actions: {
    getAnecdote(context) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/group/anecdote').then((response) => {
        let res = response.data;

        context.commit('SET_ANECDOTE_DATA', {
          data: res.result
        });

        context.commit('SET_LOADING', false);
      });
    }
  }
}