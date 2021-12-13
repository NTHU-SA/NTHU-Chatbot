<template>
<div>
  <v-dialog v-model="$store.state.editNewsDialog.visible" width="60%">
    <v-card :loading="$store.state.editNewsDialog.loading">
      <v-card-title>編輯新聞</v-card-title>
      <v-card-text>
        <v-form class="pt-4">
          <v-text-field filled label="新聞標題" v-model="$store.state.editNewsDialog.news.title" prepend-icon="title"></v-text-field>
          <v-select v-model="$store.state.editNewsDialog.news.category" :items="categoryItems" filled label="新聞類型" prepend-icon="category"></v-select>
          <v-menu v-model="dateMenu" :close-on-content-click="false" transition="scale-transition" offset-y max-width="290px" min-width="290px">
            <template v-slot:activator="{ on }">
              <v-text-field filled v-model="dateRangeText" label="活動日期" prepend-icon="event" readonly v-on="on"></v-text-field>
            </template>
            <v-date-picker v-model="$store.state.editNewsDialog.news.date" no-title @input="dateMenu=false" range></v-date-picker>
          </v-menu>
          <v-text-field filled label="活動時間" v-model="$store.state.editNewsDialog.news.time" prepend-icon="access_time" hint="example 14:00~16:00"></v-text-field>
          <v-text-field filled label="活動地點" v-model="$store.state.editNewsDialog.news.location" prepend-icon="my_location"></v-text-field>
          <v-text-field filled label="來源網址" v-model="$store.state.editNewsDialog.news.sourceUrl" prepend-icon="link" hint="Please fill in the activity source URL"></v-text-field>
          <v-text-field filled label="活動圖片網址" v-model="$store.state.editNewsDialog.news.imgUrl" prepend-icon="photo" hint="Please fill in the image URL"></v-text-field>
          <v-textarea rows="10" filled label="新聞內容" v-model="$store.state.editNewsDialog.news.content"></v-textarea>
        </v-form>
        <v-btn color="primary" style="float:right;" @click="save">確定</v-btn>
        <div class="" style="clear:both;"></div>
      </v-card-text>
    </v-card>
  </v-dialog>
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
    }
  },
  computed: {
    dateRangeText () {
      return this.$store.state.editNewsDialog.news.date.join(' ~ ')
    },
  },
  methods: {
    save() {
      axios.post('/api/v1/instanews/updateOne', {
        id: this.$store.state.editNewsDialog.news.id,
        title: this.$store.state.editNewsDialog.news.title,
        category: this.$store.state.editNewsDialog.news.category,
        date: this.$store.state.editNewsDialog.news.date,
        time: this.$store.state.editNewsDialog.news.time,
        location: this.$store.state.editNewsDialog.news.location,
        sourceUrl: this.$store.state.editNewsDialog.news.sourceUrl,
        imgUrl: this.$store.state.editNewsDialog.news.imgUrl,
        content: this.$store.state.editNewsDialog.news.content,
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '成功更新新聞！'
        });

        this.$store.dispatch('editNewsDialog/close');
        this.$store.dispatch('commonNewsWall/getNewsData');
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    }
  }
}
</script>