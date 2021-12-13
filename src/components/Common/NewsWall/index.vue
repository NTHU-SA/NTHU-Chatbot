<template>
<div>
  <v-container class="pt-0 pb-8">
    <!-- loading progress -->
    <v-row align="center" justify="center" style="height: 200px;" v-if="$store.state.commonNewsWall.loading">
      <v-progress-circular
        indeterminate
        color="primary"
        v-if="$store.state.commonNewsWall.loading"
      ></v-progress-circular>
    </v-row>
    <!-- loading progress -->

    <v-row v-else>
      <v-col cols="4" v-for="news in computedData" :key="news.id">
        <v-card class="news-card mx-auto">
          <v-img class="white--text align-end" v-if="news.imgUrl==''" height="200px" :src="require(`@/assets/instanews_img/noimage.png`)" :lazy-src="require(`@/assets/instanews_img/noimage.png`)">
            <v-card-title class="font-weight-bold">{{ news.title }}</v-card-title>
          </v-img>
          <v-img class="white--text align-end" v-else height="200px" :src="news.imgUrl" :lazy-src="news.imgUrl">
            <v-card-title class="font-weight-bold" style="background-color:rgba(0,0,0,0.60);">{{ news.title }}</v-card-title>
          </v-img>

          <v-card-subtitle class="news-card-category pb-2">
            <v-icon small>category</v-icon>
            {{ news.category }}
          </v-card-subtitle>

          <v-card-subtitle class="news-card-datetime pt-0 pb-2">
            <v-icon small>access_time</v-icon>
            <span v-if="news.date"> {{ news.date.join(' ~ ') }} &nbsp; {{ news.time }}</span>
          </v-card-subtitle>

          <v-card-subtitle class="news-card-location pt-0 pb-2">
            <v-icon small>my_location</v-icon>
            {{ news.location }}
          </v-card-subtitle>

          <v-card-subtitle class="news-card-location pt-0 pb-2">
            <v-icon small>link</v-icon>
            <a v-if="news.sourceUrl" :href="news.sourceUrl" class="ml-1" target="_blank">點我連結到活動網址</a>
            <span v-else class="ml-1">無活動網址</span>
          </v-card-subtitle>

          <v-card-text class="news-card-content text--primary">
            <div style="white-space:pre-wrap;">{{ news.content }}</div>
          </v-card-text>

          <v-card-actions>
            <v-spacer></v-spacer>
            <v-btn color="orange" text @click="editNews(news.id)">編輯</v-btn>
            <v-btn color="red darken-1" text @click="deleteNews(news.id)">刪除</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>

    <Edit-News-Dialog></Edit-News-Dialog>
  </v-container>
</div>
</template>

<script>
import axios from 'axios'

import EditNewsDialog from './EditNewsDialog'

export default {
  components: {
    EditNewsDialog
  },
  data() {
    return {
      // newsData: []
    }
  },
  computed: {
    computedData () {
      return this.$store.state.commonNewsWall.newsData.reverse();
    },
  },
  mounted() {
    this.$store.dispatch('commonNewsWall/getNewsData');
  },
  methods: {
    deleteNews(id) {
      axios.post('/api/v1/instanews/delete', {
        id: id
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '成功刪除新聞！'
        });
        
        this.$store.dispatch('commonNewsWall/getNewsData');
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    },
    editNews(id) {
      this.$store.dispatch('editNewsDialog/getNewsData', id);
      this.$store.dispatch('editNewsDialog/show');
    }
  }
}
</script>

<style lang="scss">
.news-card {
  .news-card-datetime {
    white-space: nowrap;
    overflow: auto;
  }
  .news-card-content {
    height: 100px;
    overflow: auto;
  }
}
</style>

