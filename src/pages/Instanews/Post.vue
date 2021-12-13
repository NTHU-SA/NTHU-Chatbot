<template>
<div>
  <v-container class="pt-0 pb-8">
    <section class="instanews-post-section">
      <p class="title" style="color:#F5F5F5;">文章編輯</p>
      <v-form>
        <v-text-field filled label="新聞標題" v-model="news.title" prepend-icon="title"></v-text-field>
        <v-select v-model="news.category" :items="categoryItems" filled label="新聞類型" prepend-icon="category"></v-select>
        <v-menu v-model="dateMenu" :close-on-content-click="false" transition="scale-transition" offset-y max-width="290px" min-width="290px">
          <template v-slot:activator="{ on }">
            <v-text-field filled v-model="dateRangeText" label="活動日期" prepend-icon="event" readonly v-on="on"></v-text-field>
          </template>
          <v-date-picker v-model="news.date" no-title @input="dateMenu=false" range></v-date-picker>
        </v-menu>
        <v-text-field filled label="活動時間" v-model="news.time" prepend-icon="access_time" hint="example 14:00~16:00"></v-text-field>
        <v-text-field filled label="活動地點" v-model="news.location" prepend-icon="my_location"></v-text-field>
        <v-text-field filled label="來源網址" v-model="news.sourceUrl" prepend-icon="link" hint="Please fill in the activity source URL"></v-text-field>
        <v-text-field filled label="活動圖片網址" v-model="news.imgUrl" prepend-icon="photo" hint="Please fill in the image URL"></v-text-field>
        <v-textarea rows="10" filled label="新聞內容" v-model="news.content"></v-textarea>
      </v-form>
      <v-btn color="primary" style="float:right;" @click="post">新增</v-btn>
    </section>
  </v-container>
</div>
</template>

<script>
import axios from 'axios'

export default {
  components: {},
  data() {
    return {
      // news category select items
      categoryItems: ['演講訊息', '藝術展覽', '各類活動'],
      // date picker
      dateMenu: false,
      // date: [],
      // news editor
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
    }
  },
  computed: {
    dateRangeText () {
      console.log(this.news.date);
      return this.news.date.join(' ~ ')
    },
    // computedDateFormatted() {
    //   return this.formatDate(this.date)
    // },
  },
  // watch: {
    // date picker
    // date(val) {
    //   this.dateFormatted = this.formatDate(this.date)
    // },
  // },
  methods: {
    // date picker
    // formatDate (date) {
    //   if (!date) return null

    //   const [year, month, day] = date.split('-')
    //   return `${year}-${month}-${day}`
    // },
    // parseDate (date) {
    //   if (!date) return null

    //   const [month, day, year] = date.split('/')
    //   return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`
    // },
    
    async getNewsData() {
      let response = await axios.get('/api/v1/instanews/news', {
        params: {
          category: 'all'
        }
      });
      let data = response.data.result;
      return data
    },

    // post
    async post() {
      // check duplicated
      let newsData = await this.getNewsData();
      newsData.forEach(item => {
        if (this.news.title == item.title) {
          this.$store.dispatch('snackbar/openSnackbar', {
            msg: 'Error: 已經有重複的活動！'
          });
          return
        }
      });

      // check image
      if (this.news.imgUrl.includes('.png') || this.news.imgUrl.includes('.jpg') || this.news.imgUrl=='') {
        // pass
      } else {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: 'Error: 請填寫符合.png/.jpg格式的圖片網址'
        });
        return
      }

      // check image https
      if (this.news.imgUrl!='' && !this.news.imgUrl.includes('https')) {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: 'Error: 請使用開頭為https的圖片網址'
        });
        return
      }

      console.log('date:', this.news.date);

      axios.post('/api/v1/instanews/post', {
        title: this.news.title,
        category: this.news.category,
        date: this.news.date,
        time: this.news.time,
        location: this.news.location,
        sourceUrl: this.news.sourceUrl,
        imgUrl: this.news.imgUrl,
        content: this.news.content
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '發佈新聞成功'
        });

        this.news = {
          title: '',
          category: '',
          date: [],
          time: '',
          location: '',
          sourceUrl: '',
          imgUrl: '',
          content: ''
        }
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    }
  }
}
</script>

<style lang="scss">
</style>

