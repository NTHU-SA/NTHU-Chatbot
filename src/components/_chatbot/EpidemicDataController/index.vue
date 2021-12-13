<template>
<div>
  <section class="epidemic-data-controller-section">
    <v-row class="controller-btns">
      <v-col :cols="12">
        <v-btn class="mb-2" :loading="$store.state.epidemic.loading" :disabled="$store.state.epidemic.loading" color="warning" @click="reload">
          <v-icon left>cached</v-icon>重新整理資料
          <template v-slot:loader>
            <span class="custom-loader">
              <v-icon light>cached</v-icon>
            </span>
          </template>
        </v-btn>
      </v-col>
    </v-row>
    
    <!-- loading progress -->
    <v-row align="center" justify="center" style="height: 200px;" v-if="$store.state.epidemic.loading">
      <v-progress-circular
        indeterminate
        color="primary"
        v-if="$store.state.epidemic.loading"
      ></v-progress-circular>
    </v-row>
    <!-- loading progress -->

    <!-- 特別處理 全體學生相關懶人包(students) start -->
    <section class="students-section" v-if="auth=='group'">
      <v-card class="mb-10 data-card" v-if="!$store.state.epidemic.loading">
        <v-card-title>
          {{ $store.state.epidemic.students.title }} 
          <v-chip small class="ml-4">{{ $store.state.epidemic.students.category }}</v-chip>
        </v-card-title>

        <v-card-text>
          <v-row>
            <v-col cols="4" v-for="(ctx, idx) in $store.state.epidemic.students.content" :key="idx">
              <v-card flat>
                <v-img style="position:relative" min-height="50px" :src="ctx" :lazy-src="ctx" class="white--text align-end"></v-img>
                
                <v-btn fab style="position:absolute; top:10px; left:10px;" x-small color="red" @click="delImgarea(idx)">
                  <v-icon>clear</v-icon>
                </v-btn>
                <v-text-field label="圖片網址" dense class="mt-4" filled v-model="$store.state.epidemic.students.content[idx]"></v-text-field>
              </v-card>
            </v-col>
          </v-row>
        </v-card-text>
        
        <v-card-actions class="pt-0">
          <v-spacer></v-spacer>
          <v-btn class="mr-1" @click="addImgarea($store.state.epidemic.students.id)">新增圖片欄位</v-btn>
          <v-btn class="mr-1" color="primary" @click="updateStud">儲存</v-btn>
        </v-card-actions>
      </v-card>
    </section>
    <!-- 特別處理 全體學生相關懶人包(students) end -->


    <v-card class="mb-10 data-card" v-if="!$store.state.epidemic.loading" v-for="epid in this.$store.state.epidemic.data" :key="epid.id">
      <v-card-title>
        {{ epid.title }} 
        <v-chip small class="ml-4">{{ epid.category }}</v-chip>
      </v-card-title>
      
      <v-card-text>
        <v-row class="data-editor">
          <v-col class="data-wrapper" cols="12">
            <v-row class="pt-0 pb-0" v-for="(ctx, idx) in epid.content" :key="idx">
              <v-col class="pb-0 pt-0" cols="11" >
                <v-textarea style="white-space: pre-line;" filled rows="3" label="" v-model="epid.content[idx]"></v-textarea>
              </v-col>
              <v-col cols="1">
                <v-btn small color="red" @click="delTextarea(epid.id, idx)">刪除</v-btn>
              </v-col>
            </v-row>
          </v-col>
        </v-row>
      </v-card-text>

      <v-card-actions class="pt-0">
        <v-spacer></v-spacer>
        <v-btn class="mr-1" @click="addTextarea(epid.id)">新增訊息欄位</v-btn>
        <v-btn class="mr-1" color="primary" @click="updateEpid(epid.id)">儲存</v-btn>
      </v-card-actions>
    </v-card>
      
  </section>
</div>
</template>

<script>
import axios from 'axios';

export default {
  props: ['auth'],
  components: {},
  data() {
    return {}
  },
  mounted() {
    this.$store.dispatch('epidemic/getEpidData');
  },
  methods: {
    /* controller btns */
    reload() {
      this.$store.dispatch('epidemic/getEpidData');
    },
    /* 特別處理 students */
    delImgarea(areaIdx) {
      this.$store.state.epidemic.students.content.splice(areaIdx, 1);
    },
    addImgarea() {
      if (this.$store.state.epidemic.students.content.length == 10) {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '新增失敗，圖片訊息最多10則！'
        });
        return
      }

      this.$store.state.epidemic.students.content.push('');
    },
    updateStud() {
      axios.post('/api/v1/group/updateOneEpid', {
        category: this.$store.state.epidemic.students.category,
        content: this.$store.state.epidemic.students.content
      }).then((response) => {
        let res = response.data;

        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '更新成功！'
        });
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    },
    /* 防疫訊息 cards */
    delTextarea(id, textareaIdx) {
      this.$store.state.epidemic.data.forEach(item => {
        if (item.id == id) {
          item.content.splice(textareaIdx, 1);
          return
        }
      });
    },
    addTextarea(id) {
      this.$store.state.epidemic.data.forEach(item => {
        if (item.id == id) {
          item.content.push('');
          return
        }
      });
    },
    updateEpid(id) {
      this.$store.state.epidemic.data.forEach(item => {
        if (item.id == id) {
          axios.post('/api/v1/group/updateOneEpid', {
            category: item.category,
            content: item.content
          }).then((response) => {
            let res = response.data;

            this.$store.dispatch('snackbar/openSnackbar', {
              msg: '更新成功！'
            });
          }).catch((error) => {
            this.$store.dispatch('snackbar/openSnackbar', {
              msg: error.response.data.msg
            });
          });
          return
        }
      });
    }
  }
}
</script>