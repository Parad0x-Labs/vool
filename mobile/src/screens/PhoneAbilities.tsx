import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Alert, AppState, Pressable, Text, View} from 'react-native';
import {Audio} from 'expo-av';
import * as DocumentPicker from 'expo-document-picker';
import {CompanionError} from '../protocol/client.ts';
import * as ImagePicker from 'expo-image-picker';
import * as Location from 'expo-location';
import * as FileSystem from 'expo-file-system';
import type {MobileCompanionClient} from '../protocol/client.ts';
import {HoldRecording} from '../security/holdRecording.ts';
import {tr} from '../i18n/index.ts';

const ink = {color:'#d3d9e0', padding:10};
function consent(title: string, message: string): Promise<boolean> {
  return new Promise(resolve => Alert.alert(title,message,[{text:tr("Cancel"),style:'cancel',onPress:()=>resolve(false)},
    {text:tr("Continue"),onPress:()=>resolve(true)}],{cancelable:true,onDismiss:()=>resolve(false)}));
}
async function removeCapture(uri: string | null) {
  if (uri && FileSystem.cacheDirectory && uri.startsWith(FileSystem.cacheDirectory)) await FileSystem.deleteAsync(uri,{idempotent:true});
}

export default function PhoneAbilities({client,chatId,attachmentCount,onChat,onAttachment,onDraft}: {
  client:MobileCompanionClient; chatId:string|null; attachmentCount:number; onChat:(id:string)=>void; onAttachment:(id:string)=>void; onDraft:(text:string)=>void;
}) {
  const [recording,setRecording]=useState(false), [busy,setBusy]=useState(false), [note,setNote]=useState(''), [voiceReady,setVoiceReady]=useState(false);
  const armed=useRef(false);
  const current = useRef({chatId,onChat,onAttachment,onDraft});current.current={chatId,onChat,onAttachment,onDraft};
  const alive=useRef(true), epoch=useRef(0);
  const fail=(error:unknown)=>{if(alive.current)setNote(error instanceof CompanionError && /^(speech_|audio_|dictation_)/.test(error.code) ? tr("Voice notes are unavailable on your computer right now. Type your message instead.") : tr("Could not use this ability. Check its permission on your computer and phone."));};
  async function prepareVoice() {
    setNote('');const attempt=epoch.current;
    try {
      await client.checkAbility('microphone');
      if(!alive.current || !await consent(tr("Hold to talk"),tr("Record a short note; your computer writes it out as text for you to check.")))return;
      const permission=await Audio.requestPermissionsAsync();
      if(alive.current && attempt===epoch.current && permission.granted){armed.current=true;setVoiceReady(true);}
    }catch(error){fail(error);}
  }
  const hold=useMemo(()=>new HoldRecording(async()=>{
    if(!armed.current)return false;
    armed.current=false;
    await client.checkAbility('microphone');
    return alive.current;
  },()=>{
    const take=new Audio.Recording();let uri:string|null=null, unloaded=false;
    async function stop(){try{if(!unloaded){uri=take.getURI();await take.stopAndUnloadAsync();unloaded=true;}}finally{await Audio.setAudioModeAsync({allowsRecordingIOS:false,staysActiveInBackground:false});}}
    return {prepare:async()=>{await Audio.setAudioModeAsync({allowsRecordingIOS:true,playsInSilentModeIOS:true,staysActiveInBackground:false});await take.prepareToRecordAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);},
      start:async()=>{await take.startAsync();},
      finish:async()=>{try{await stop();if(!uri)throw new Error('missing recording');const info=await FileSystem.getInfoAsync(uri);if(!info.exists || info.size>3*1024*1024)throw new Error('recording too large');return new Uint8Array(await(await fetch(uri)).arrayBuffer());}finally{await removeCapture(uri);}},
      discard:async()=>{try{await stop();}finally{await removeCapture(uri || take.getURI());}}};
  },async bytes=>(await client.dictate(bytes)).text,text=>{if(alive.current)current.current.onDraft(text);},value=>{if(alive.current)setRecording(value);},fail),[client]);
  useEffect(()=>{
    alive.current=true;
    const sub=AppState.addEventListener('change',state=>{if(state!=='active'){epoch.current++;armed.current=false;setVoiceReady(false);void hold.end(true);}});
    return ()=>{alive.current=false;epoch.current++;sub.remove();void hold.end(true);};
  },[hold]);
  useEffect(()=>{epoch.current++;armed.current=false;setVoiceReady(false);void hold.end(true);},[chatId,hold]);
  async function file(){
    if(busy || attachmentCount>=4)return;setBusy(true);setNote('');const attempt=++epoch.current;let uri:string|null=null;
    const valid=()=>alive.current && attempt===epoch.current;
    try {
      await client.attachmentLimits();if(!valid())return;
      const result=await DocumentPicker.getDocumentAsync({copyToCacheDirectory:true,multiple:false});
      if(result.canceled || !result.assets?.[0])return;
      const asset=result.assets[0];uri=asset.uri;if(!valid())return;
      const info=await FileSystem.getInfoAsync(uri);
      if(!info.exists || info.isDirectory || info.size<=0 || info.size>3*1024*1024)throw new Error('file too large');
      const bytes=new Uint8Array(await(await fetch(uri)).arrayBuffer());if(!valid())return;
      const id=current.current.chatId || (await client.prepareChat()).chat_id;if(!valid())return;
      const record=await client.uploadAttachment(id,asset.name,bytes,{kind:'file',mediaType:asset.mimeType || 'application/octet-stream'});
      if(valid()){current.current.onChat(id);current.current.onAttachment(record.id);}
    }catch(error){if(valid())fail(error);}finally{await removeCapture(uri).catch(fail);if(alive.current)setBusy(false);}
  }
  async function photo(camera:boolean) {
    if(busy || attachmentCount>=4)return;setBusy(true);setNote('');const attempt=++epoch.current;let uri:string|null=null;
    const valid=()=>alive.current && attempt===epoch.current;
    try {
      await client.checkAbility(camera?'camera':'photos');if(!valid())return;
      if(!await consent(camera?tr("Take a photo"):tr("Choose a photo"),tr("Add one photo to this chat. Nothing is sent as a message until you tap Send.")) || !valid())return;
      const permission=camera?await ImagePicker.requestCameraPermissionsAsync():await ImagePicker.requestMediaLibraryPermissionsAsync();
      if(!permission.granted || !valid())return;
      const options={mediaTypes:ImagePicker.MediaTypeOptions.Images,quality:0.6,allowsMultipleSelection:false};
      const result=camera?await ImagePicker.launchCameraAsync(options):await ImagePicker.launchImageLibraryAsync(options);
      if(result.canceled || !result.assets?.[0])return;
      const asset=result.assets[0];uri=asset.uri;if(!valid())return;
      const info=await FileSystem.getInfoAsync(uri);
      if(!info.exists || info.isDirectory || info.size<=0 || info.size>3*1024*1024)throw new Error('photo too large');
      const bytes=new Uint8Array(await(await fetch(uri)).arrayBuffer());
      if(bytes.length>3*1024*1024)throw new Error('photo too large');
      const id=current.current.chatId || (await client.prepareChat()).chat_id;if(!valid())return;
      const file=await client.uploadAttachment(id,asset.fileName || 'photo.jpg',bytes,{kind:'photo',mediaType:asset.mimeType || 'image/jpeg',ability:camera?'camera':'photos'});
      if(valid()){current.current.onChat(id);current.current.onAttachment(file.id);}
    }catch(error){if(valid())fail(error);}finally{await removeCapture(uri).catch(fail);if(alive.current)setBusy(false);}
  }
  async function location(){
    if(busy)return;setBusy(true);setNote('');const attempt=++epoch.current;
    const valid=()=>alive.current && attempt===epoch.current;
    try {
      await client.checkAbility('location');if(!valid())return;
      if(!await consent(tr("Share location"),tr("Add your current location to the message box for review. No background location is used.")) || !valid())return;
      if(!(await Location.requestForegroundPermissionsAsync()).granted || !valid())return;
      const point=await Location.getCurrentPositionAsync({accuracy:Location.Accuracy.Balanced});if(!valid())return;
      const result=await client.locationDraft(point.coords.latitude,point.coords.longitude);
      if(valid())current.current.onDraft(result.text);
    }catch(error){if(valid())fail(error);}finally{if(alive.current)setBusy(false);}
  }
  return <View><View style={{flexDirection:'row',flexWrap:'wrap'}}>
    <Pressable disabled={busy || recording || attachmentCount>=4} onPress={()=>void file()}><Text style={ink}>{tr("Choose a file")}</Text></Pressable>
    <Pressable disabled={busy || recording || attachmentCount>=4} onPress={()=>void photo(false)}><Text style={ink}>{tr("Choose a photo")}</Text></Pressable>
    <Pressable disabled={busy || recording || attachmentCount>=4} onPress={()=>void photo(true)}><Text style={ink}>{tr("Take a photo")}</Text></Pressable>
    <Pressable disabled={busy} onPress={()=>void location()}><Text style={ink}>{tr("Share location")}</Text></Pressable>
    {!voiceReady && !recording ? <Pressable disabled={busy} onPress={()=>void prepareVoice()}><Text style={ink}>{tr("Voice note")}</Text></Pressable> : <Pressable disabled={busy} onPressIn={()=>{setNote('');void hold.begin();}} onPressOut={()=>{setBusy(true);void hold.end().finally(()=>{if(alive.current){setVoiceReady(false);setBusy(false);}});}}><Text style={ink}>{recording?tr("Let go and it becomes text"):tr("Hold to talk")}</Text></Pressable>}
  </View>{note?<Text style={ink}>{note}</Text>:null}</View>;
}
