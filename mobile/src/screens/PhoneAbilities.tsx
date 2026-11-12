import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Alert, AppState, Pressable, StyleSheet} from 'react-native';
import Icon from '../ui/Icon.tsx';
import Sheet from '../ui/Sheet.tsx';
import {C} from '../ui/theme.ts';
import {Audio} from 'expo-av';
import * as DocumentPicker from 'expo-document-picker';
import {CompanionError} from '../protocol/client.ts';
import * as ImagePicker from 'expo-image-picker';
import * as Location from 'expo-location';
import * as FileSystem from 'expo-file-system';
import type {MobileCompanionClient} from '../protocol/client.ts';
import {HoldRecording} from '../security/holdRecording.ts';
import {tr} from '../i18n/index.ts';

function consent(title: string, message: string): Promise<boolean> {
  return new Promise(resolve => Alert.alert(title,message,[{text:tr("Cancel"),style:'cancel',onPress:()=>resolve(false)},
    {text:tr("Continue"),onPress:()=>resolve(true)}],{cancelable:true,onDismiss:()=>resolve(false)}));
}
async function removeCapture(uri: string | null) {
  if (uri && FileSystem.cacheDirectory && uri.startsWith(FileSystem.cacheDirectory)) await FileSystem.deleteAsync(uri,{idempotent:true});
}

/** The message box's "+" choices (file, photo, camera, location) and its microphone button.
 *  It draws the microphone in place and the choices as a sheet; words for the person go to onNote. */
export default function PhoneAbilities({client,chatId,attachmentCount,onChat,onAttachment,onDraft,sheetOpen,onSheetClose,onNote}: {
  client:MobileCompanionClient; chatId:string|null; attachmentCount:number; onChat:(id:string)=>void; onAttachment:(id:string)=>void; onDraft:(text:string)=>void;
  sheetOpen:boolean; onSheetClose:()=>void; onNote:(text:string)=>void;
}) {
  const [recording,setRecording]=useState(false), [busy,setBusy]=useState(false), [voiceReady,setVoiceReady]=useState(false);
  const noteRef=useRef(onNote);noteRef.current=onNote;
  const hinting=useRef(false);
  const setNote=(text:string)=>{if(text)hinting.current=false;noteRef.current(text);};
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
  // The hint for the armed microphone shares the note line; an error shown there is never wiped by it.
  useEffect(()=>{
    if(recording||voiceReady){hinting.current=true;noteRef.current(recording?tr("Let go and it becomes text"):tr("Hold to talk"));}
    else if(hinting.current){hinting.current=false;noteRef.current('');}
  },[recording,voiceReady]);
  const full=attachmentCount>=4;
  return <>
    <Sheet open={sheetOpen} onClose={onSheetClose} actions={[
      {key:'file',label:tr("Choose a file"),icon:'paperclip',disabled:busy||recording||full,onPress:()=>void file()},
      {key:'photo',label:tr("Choose a photo"),icon:'image-outline',disabled:busy||recording||full,onPress:()=>void photo(false)},
      {key:'camera',label:tr("Take a photo"),icon:'camera-outline',disabled:busy||recording||full,onPress:()=>void photo(true)},
      {key:'location',label:tr("Share location"),icon:'map-marker-outline',disabled:busy,onPress:()=>void location()},
    ]} />
    {!voiceReady && !recording
      ? <Pressable disabled={busy} onPress={()=>void prepareVoice()} style={styles.mic} hitSlop={6}
          accessibilityRole="button" accessibilityLabel={tr("Voice note")}>
          <Icon name="microphone-outline" size={22} color={C.soft} />
        </Pressable>
      : <Pressable disabled={busy} style={[styles.mic,recording?styles.live:styles.ready]} hitSlop={6}
          accessibilityRole="button" accessibilityLabel={recording?tr("Let go and it becomes text"):tr("Hold to talk")}
          onPressIn={()=>{setNote('');void hold.begin();}}
          onPressOut={()=>{setBusy(true);void hold.end().finally(()=>{if(alive.current){setVoiceReady(false);setBusy(false);}});}}>
          <Icon name="microphone" size={22} color={recording?'#fff':C.sendInk} />
        </Pressable>}
  </>;
}
const styles=StyleSheet.create({
  mic:{width:48,height:48,borderRadius:24,alignItems:'center',justifyContent:'center'},
  ready:{backgroundColor:C.send,width:48},
  live:{backgroundColor:C.bad,width:48},
});
