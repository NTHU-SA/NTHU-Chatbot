<template>
<div>
  <v-container class="pt-0 pb-8">
    <section class="group-qa-management-section">
      <v-row class="qa-new-category">
        <v-col>
          <p class="title" style="color:#F5F5F5;">新增QA類別 (category)</p>
          <v-row>
            <v-col class="py-0" cols="4">
              <v-text-field dense label="QA Category" filled v-model="addQaCategoryValue"></v-text-field>
            </v-col>
            <v-col class="pb-0" cols="3">
              <v-btn color="primary" @click="addCategory">新增</v-btn>
            </v-col>
          </v-row>
        </v-col>
      </v-row>

      <v-divider></v-divider>

      <!-- <v-row class="qa-new-data mt-6">
        
      </v-row>

      <v-divider></v-divider> -->

      <!-- loading progress -->
      <v-row align="center" justify="center" style="height: 200px;" v-if="dataLoading">
        <v-progress-circular
          indeterminate
          color="primary"
          v-if="dataLoading"
        ></v-progress-circular>
      </v-row>
      <!-- loading progress -->

      <v-row class="qa-data mt-6" v-else>
        <v-col class="pb-0" cols="12">
          <p class="title" style="color:#F5F5F5;">QA 資料</p>
        </v-col>

        <v-col class="pt-0" :cols="4">
          <v-select v-model="categorySelectValue" :items="this.$store.state.qaManagement.category" filled label="category"></v-select>
        </v-col>

        <v-col cols="12">
          <p style="color:#F5F5F5;">新增一則QA</p>
          <v-row>
            <v-col class="py-0" cols="12">
              <v-textarea rows="1" dense label="Question" filled v-model="addQA.ques"></v-textarea>
            </v-col>
            <v-col class="py-0" cols="12">
              <v-textarea rows="3" dense label="Answer" filled v-model="addQA.ans"></v-textarea>
            </v-col>
            <v-col>
              <v-btn color="primary" width="100%" @click="addOneQA">新增</v-btn>
            </v-col>
          </v-row>
        </v-col>

        
        <v-col cols="12">
          <p style="color:#F5F5F5;">QA</p>
          <v-expansion-panels multiple>
            <v-expansion-panel v-for="(qa, idx) in QaData.data" :key="idx+1">
              
              <v-expansion-panel-header>Q： {{ qa.ques }} </v-expansion-panel-header>
              <v-expansion-panel-content style="white-space: pre-line;">
                A：
                <p>{{ qa.ans }}</p>
                <div class="panel-btn">
                  <v-btn color="error" @click="delOneQA(qa.ques)">刪除</v-btn>
                </div>
              </v-expansion-panel-content>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-col>
      </v-row>
      
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
      // add category
      addQaCategoryValue: '',
      // add qa
      addQA: {
        ques: '',
        ans: ''
      },
      // qa data
      categorySelectValue: 'epidemic',
    }
  },
  mounted() {
    this.dispatchQaData();
  },
  computed: {
    QaData() {
      let data = this.$store.state.qaManagement.data;
      
      let result = {};
      data.forEach((item) => {
        if (item.category == this.categorySelectValue) {
          result = item;
          return
        }
      });

      return result;
    },
    dataLoading() {
      return this.$store.state.qaManagement.loading;
    }
  },
  methods: {
    dispatchQaData() {
      this.$store.dispatch('qaManagement/getAllQA');
    },
    addCategory() {
      axios.post('/api/v1/group/addQACategory', {
        category: this.addQaCategoryValue
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '新增QA類別成功'
        });

        this.addQaCategoryValue = '';

        this.$store.dispatch('qaManagement/getAllQA');
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    },
    addOneQA() {
      axios.post('/api/v1/group/addOneQA', {
        category: this.categorySelectValue,
        ques: this.addQA.ques,
        ans: this.addQA.ans
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '新增一則QA成功'
        });

        this.addQA = {
          ques: '',
          ans: ''
        }

        this.$store.dispatch('qaManagement/getAllQA');
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    },
    delOneQA(ques) {
      axios.post('/api/v1/group/delOneQA', {
        category: this.categorySelectValue,
        ques: ques
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '刪除一則QA成功'
        });

        this.$store.dispatch('qaManagement/getAllQA');
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
.panel-btn {
  float: right;
}
</style>

