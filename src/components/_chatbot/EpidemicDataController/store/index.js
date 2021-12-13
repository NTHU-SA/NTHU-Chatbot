import axios from 'axios'

export default {
  namespaced: true,
  state: {
    loading: false,
    students: {}, // 特別處理 全體學生相關懶人包(students)
    data: [] // 其他防疫資訊
  },
  mutations: {
    SET_LOADING(state, isLoading) {
      state.loading = isLoading;
    },

    SET_DATA(state, options) {
      state[options.key] = options.data;
    }
  },
  actions: {
    getEpidData(context) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/group/getAllEpid').then((response) => {
        let res = response.data;

        context.commit('SET_DATA', {
          key: 'students',
          data: res.result[0]
        });

        context.commit('SET_DATA', {
          key: 'data',
          data: res.result.slice(1, res.result.length)
        });

        context.commit('SET_LOADING', false);
      });
    },
  }
}