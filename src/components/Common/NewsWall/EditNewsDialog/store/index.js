import axios from 'axios'

export default {
  namespaced: true,
  state: {
    visible: false,
    loading: false,
    
    // data
    news: {
      title: '',
      category: '',
      date: [],
      time: '',
      location: '',
      sourceUrl: '',
      imgUrl: '',
      content: ''
    }
  },
  mutations: {
    // visible
    SET_VISIBLE(state, status) {
      state.visible = status;
    },
    SET_LOADING(state, status) {
      state.loading = status;
    },

    // data
    SET_DATA(state, data) {
      state.news = data;
    }
  },
  actions: {
    // visible
    show(context) {
      context.commit('SET_VISIBLE', true);
    },
    close(context) {
      context.commit('SET_VISIBLE', false);
    },

    // Data
    getNewsData(context, id) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/instanews/getOne', {
        params: {
          id: id
        }
      }).then((response) => {
        let res = response.data;
        context.commit('SET_DATA', res.result);
        
        context.commit('SET_LOADING', false);
      }).catch((error) => {
        console.log(error);
      });
    }
  }
}