<template>
<div>
  <section class="push-component-editor-section">
    <p class="title" style="color:#F5F5F5;">文章編輯</p>
    <v-select :items="contentTypeItems" v-model="contentType" label="Type" style="width:20%;"></v-select>
    
    <div class="editor-text" v-if="contentType=='text'">
      <v-textarea filled label="文章內容" v-model="content"></v-textarea>
    </div>
    
    <div class="editor-image" v-if="contentType=='image'">
      <v-text-field label="圖片網址" filled v-model="imgUrl"></v-text-field>
    </div>

    <div class="editor-sticker" v-if="contentType=='sticker'">
      <v-item-group v-model="stickerID">
        <div class="sticker-package" v-for="pkg in stickerPackage" :key="pkg.id">
          <p class="title mb-0" style="color:#F5F5F5;">{{ pkg.name }}</p>
          <v-row>
            <v-col cols="2" v-for="id in pkg.stickerID" :key="id">
              <v-item v-slot:default="{ active, toggle }" :value="pkg.id+'/'+id">
                <v-card :color="active ? 'primary' : ''" class="d-flex align-center" dark @click="toggle">
                  <v-img height="150px" :src="require(`../../assets/sticker_img/${pkg.id}/${id}@2x.png`)"></v-img>
                </v-card>
              </v-item>
            </v-col>
          </v-row>
        </div>
      </v-item-group>
    </div>
    
    <v-btn color="primary" width="100%" @click="addToMsg">新增</v-btn>
  </section>

  <section class="push-component-editor-preview-section" v-if="messages.length>0">
    <p class="title" style="color:#F5F5F5;">內容預覽</p>
    <v-row>
      <v-col cols="3" v-for="(item, idx) in messages" :key="idx+1">
        <v-card class="editor-preview-card mx-auto" dark>
          <v-card-text class="card-content" v-if="item.type=='text'">
            <v-row align="center" class="mx-0">{{ item.text }}</v-row>
          </v-card-text>
          <v-img height="150" :src="item.originalContentUrl" v-if="item.type=='image'"></v-img>
          <v-img height="150" :src="require(`../../assets/sticker_img/${item.packageId}/${item.stickerId}@2x.png`)" v-if="item.type=='sticker'"></v-img>
          <v-divider dark></v-divider>
          <v-card-actions>
            <v-chip small color="orange" label text-color="white">
              <v-icon left small>label</v-icon>{{ item.type }}</v-chip>
            <v-spacer />
            <v-btn color="deep-orange" text @click="removeMessage(idx)">刪除</v-btn>
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
  </section>

  <v-divider dark></v-divider>

  <section class="push-component-choose-user-section">
    <p class="title" style="color:#F5F5F5;">選擇推播對象</p>
    <v-data-table
      v-model="selected"
      :headers="headers"
      :items="userTableData"
      item-key="userID"
      show-select
      class="elevation-1"
    >
    </v-data-table>
  </section>

  <v-btn color="primary" style="float:right;" @click="submit">submit</v-btn>
</div>
</template>

<script>
import axios from 'axios'

export default {
  components: {},
  props: ['category'],
  data() {
    return {
      // sticker
      stickerPackage: [{
        id: '11537',
        name: 'Brown, Cony & Sally',
        stickerID: ['52002734', '52002735', '52002736', '52002737', '52002738', '52002739', '52002740', '52002741', '52002742', '52002743', '52002744', '52002745', '52002746', '52002747', '52002748', '52002749', '52002750', '52002751', '52002752', '52002753', '52002754', '52002755', '52002756', '52002757', '52002758', '52002759', '52002760', '52002761', '52002762', '52002763', '52002764', '52002765', '52002766', '52002767', '52002768', '52002769', '52002770', '52002771', '52002772', '52002773']
      }, {
        id: '11538',
        name: 'CHOCO & Friends',
        stickerID: ['51626494', '51626495', '51626496', '51626497', '51626498', '51626499', '51626500', '51626501', '51626502', '51626503', '51626504', '51626505', '51626506', '51626507', '51626508', '51626509', '51626510', '51626511', '51626512', '51626513', '51626514', '51626515', '51626516', '51626517', '51626518', '51626519', '51626520', '51626521', '51626522', '51626523', '51626524', '51626525', '51626526', '51626527', '51626528', '51626529', '51626530', '51626531', '51626532', '51626533']
      }, {
        id: '11539',
        name: 'UNIVERSTAR BT21',
        stickerID: ['52114110', '52114111', '52114112', '52114113', '52114114', '52114115', '52114116', '52114117', '52114118', '52114119', '52114120', '52114121', '52114122', '52114123', '52114124', '52114125', '52114126', '52114127', '52114128', '52114129', '52114130', '52114131', '52114132', '52114133', '52114134', '52114135', '52114136', '52114137', '52114138', '52114139', '52114140', '52114141', '52114142', '52114143', '52114144', '52114145', '52114146', '52114147', '52114148', '52114149']
      }],
      // editor
      contentTypeItems: ['text', 'image', 'sticker'],
      contentType: 'text',
      content: "", // type: text
      imgUrl: "", // type: image
      stickerID: "",
      // result messages
      messages: [],
      // choose users table
      selected: [],
      headers: [
        { 
          text: 'Line 使用者ID',
          align: 'left',
          sortable: false,
          value: 'userID',
        }
      ],
      userTableData: [],
    }
  },
  mounted() {
    this.getUserInfo();
  },
  methods: {
    getUserInfo() {
      let userInfoApi = '';
      if (this.category == "official") {
        userInfoApi = '/api/v1/admin/userInfo';
      } else if (this.category == "beta") {
        userInfoApi = '/api/beta/admin/userInfo';
      }

      axios.get(userInfoApi).then((response) => {
        let res = response.data;
        this.userTableData = res.result;
      }).catch((error) => {
        console.log(error);
      });
    },
    addToMsg() {
      if (this.contentType == 'text') {
        let obj = {
          'type': 'text',
          'text': this.content
        }
        this.messages.push(obj);

        this.content = '';
      } else if (this.contentType == 'image') {
        let obj = {
          'type': 'image',
          'originalContentUrl': this.imgUrl,
          'previewImageUrl': this.imgUrl
        }
        this.messages.push(obj);

        this.imgUrl = '';
      } else if (this.contentType == 'sticker') {
        let stickerIDSplit = this.stickerID.split('/');
        let pkgID = stickerIDSplit[0];
        let stkID = stickerIDSplit[1];
        let obj = {
          'type': 'sticker',
          'packageId': pkgID,
          'stickerId': stkID
        }
        this.messages.push(obj);

        this.stickerID = '';
      }
    },
    removeMessage(idx) {
      this.messages.splice(idx, 1);
    },
    submit() {
      if (this.messages.length == 0) {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '請輸入要推播的內容'
        });
        return;
      }

      if (this.selected.length == 0) {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '請選擇要推播的使用者'
        });
        return;
      }

      // 取 selected line id
      let selectedLineID = [];
      this.selected.forEach((item) => {
        selectedLineID.push(item.userID);
      });


      let pushApi = '';
      if (this.category == "official") {
        pushApi = '/api/v1/msg/push';
      } else if (this.category == "beta") {
        pushApi = '/api/beta/msg/push';
      }
      
      axios.post(pushApi, {
        to: selectedLineID,
        messages: this.messages,
      }).then((response) => {
        let res = response.data;
        
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '推播成功！'
        });

        // clear messages
        this.content = '';
        this.originalContentUrl = '';
        this.previewImageUrl = '';
        this.messages = [];

        // clear user
        this.selected = [];
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
  .push-component-editor-section {
    margin-bottom: 20px;
  }

  .push-component-editor-preview-section {
    .editor-preview-card .card-content {
      overflow: auto;
      height: 150px;
    }
  }

  .push-component-choose-user-section {
    margin-top: 20px;
    margin-bottom: 20px;
  }
</style>

