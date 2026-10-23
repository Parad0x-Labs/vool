import React, {useCallback, useEffect, useMemo, useState} from "react";
import {Alert, Pressable, Text, TextInput, View} from "react-native";
import {errorText, tr} from "../i18n/index.ts";
import {CompanionError, type MobileCompanionClient} from "../protocol/client.ts";
import type {DeviceInfo} from "../protocol/models.ts";
import {PaymentDraft} from "../protocol/paymentDraft.ts";
import {SecureProposalStore} from "../store/paymentDraft.ts";

type WalletView = Awaited<ReturnType<MobileCompanionClient["walletStatus"]>>;
const button = {backgroundColor: "#242a32", padding: 12, borderRadius: 8, marginVertical: 6};
const text = {color: "#e8ecf1", marginVertical: 6};
const input = {...button, color: "#e8ecf1", borderWidth: 1, borderColor: "#404852"};

function receiptStatus(state: string): string {
  if (state === "confirmed") return tr("Completed");
  if (state === "rejected") return tr("Declined");
  if (state === "expired") return tr("Expired");
  if (state === "failed") return tr("Failed");
  return tr("Check the payment on your computer. It may already have been sent.");
}

export default function WalletScreen({client, onRevoked}: {client: MobileCompanionClient; onRevoked(): void | Promise<void>}) {
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  const [view, setView] = useState<WalletView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState(true);
  const [balance, setBalance] = useState("");
  const [destination, setDestination] = useState("");
  const [amount, setAmount] = useState("");
  const [saved, setSaved] = useState(false);
  const [notice, setNotice] = useState("");
  const [matches,setMatches]=useState<{display_name:string}[]>([]);
  const [receipts, setReceipts] = useState<Record<string, unknown>[]>([]);
  const draft = useMemo(() => new PaymentDraft(new SecureProposalStore(client.deviceId), client, review=>new Promise(resolve=>{
    const isolate=(value:string)=>"\u2066"+value+"\u2069";
    const warnings=review.warnings.map(reason=>reason==="new_payee"?tr("New payee"):tr("Unusual amount"));
    Alert.alert(tr("Review payment"), [...warnings,isolate(review.amount+" "+review.asset),isolate(review.network),isolate(review.destination),
      tr("Check the payee and amount. Never share your wallet’s secret phrase or private key."),
      tr("Review fees and approve this payment on your computer."),tr("Completed payments cannot be undone.")].join("\n"),
      [{text:tr("Cancel"),style:"cancel",onPress:()=>resolve(false)},{text:tr("Confirm proposal"),onPress:()=>resolve(true)}],
      {cancelable:true,onDismiss:()=>resolve(false)});
  })), [client]);
  const refresh = useCallback(async () => {
    setError("");
    try {
      const next = await client.devicesMe(); setDevice(next);
      if (next.rules?.wallet_view) setView(await client.walletStatus());
      else setView(null);
      const existing = await draft.load(); setSaved(!!existing);
      if (existing) {setDestination(existing.destination); setAmount(existing.amount);}
    } catch (exc) {
      if (exc instanceof CompanionError && exc.code === "device_revoked") void onRevoked();
      setError(errorText(exc));
    }
  }, [client, draft, onRevoked]);
  useEffect(() => {void refresh();}, [refresh]);
  useEffect(()=>{
    let current=true;setMatches([]);
    if(!device?.rules?.wallet_view || saved || !destination.trim())return;
    const timer=setTimeout(()=>{void client.walletContacts(destination).then(value=>{if(current)setMatches(value.contacts.slice(0,8));}).catch(()=>{});},250);
    return ()=>{current=false;clearTimeout(timer);};
  },[client,device,destination,saved]);
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try {await work(); await refresh();}
    catch (exc) {if ((exc as {code?:string}).code !== "review_cancelled") setError(errorText(exc)); try {setSaved(!!await draft.load());} catch {setSaved(true);}}
    finally {setBusy(false);}
  };
  const freeze = () => void act(async () => {await client.freezePayments(); setNotice(tr("Payments are frozen."));});
  return <View>
    <Text style={{...text, fontSize: 22}}>{tr("Wallet")}</Text>
    <Text style={text}>{tr("Your wallet stays on your computer. Approve payments there.")}</Text>
    <Pressable style={button} disabled={busy} onPress={freeze}><Text style={text}>{tr("Freeze payments")}</Text></Pressable>
    {view && !view.status.enabled && <Text style={text}>{tr("Crypto is off on your computer.")}</Text>}
    {view?.frozen && <Text style={text}>{tr("Payments are frozen. Unfreeze on your computer with the wallet PIN.")}</Text>}
    {error ? <Text style={{...text, color: "#f2aa98"}} accessibilityRole="alert">{error}</Text> : null}
    {notice ? <Text style={text}>{notice}</Text> : null}
    <Pressable style={button} disabled={busy} onPress={() => void refresh()}><Text style={text}>{tr("Refresh")}</Text></Pressable>
    {!device?.rules?.wallet_view ? <Text style={text}>{tr("Wallet viewing is off. Change permissions on your computer.")}</Text> : <>
      <Pressable style={button} onPress={() => {
        setHidden(!hidden);
        const accountId = device.payment_cap?.wallet_id || view?.status.accounts?.[0]?.wallet_id;
        if (hidden && accountId) void act(async () => {
          const answer = await client.walletBalance(accountId);
          setBalance(answer.state === "read" ? `${answer.balance_human} ${answer.asset}` : tr("Balance unavailable. Try again later."));
        });
      }}><Text style={text}>{hidden ? tr("Show balances") : tr("Hide balances")}</Text></Pressable>
      {!hidden && <><Text style={text}>{balance || tr("Choose an account to show its balance.")}</Text>
        <Text style={text}>{tr("This balance shows only the network's native coin; tokens are not included.")}</Text></>}
      {!hidden && (view?.status.accounts || []).map(account => <Pressable key={account.wallet_id} style={button} disabled={busy}
        onPress={() => void act(async () => {
          const answer = await client.walletBalance(account.wallet_id);
          setBalance(answer.state === "read" ? `${answer.balance_human} ${answer.asset}` : tr("Balance unavailable. Try again later."));
        })}><Text style={text}>{account.label || tr("Wallet account")} · {account.network}</Text></Pressable>)}
      <Text style={text}>{tr("Pending payments")}</Text>
      {(view?.status.pending || []).map(row => <View key={row.proposal_id} style={{paddingVertical: 8}}>
        <Text style={[text, {writingDirection: "ltr"}]}>{row.amount_human} {row.display_symbol} · {row.network}</Text>
        <Text style={[text, {writingDirection: "ltr", textAlign: "left"}]}>{row.destination}</Text>
        <Text style={text}>{tr("Review fees and approve this payment on your computer.")}</Text>
        {row.mobile_owned && <Pressable style={button} disabled={busy} onPress={() => Alert.alert(tr("Decline payment?"), tr("Completed payments cannot be undone."), [
          {text: tr("Cancel"), style: "cancel"}, {text: tr("Decline"), style: "destructive", onPress: () => void act(async () => {
            const answer = await client.declinePayment(row.proposal_id);
            setNotice(answer.cancelled ? tr("Payment declined. Nothing was sent.") : tr("Check the payment on your computer. It may already have been sent."));
          })}])}><Text style={text}>{tr("Decline this phone’s request")}</Text></Pressable>}
      </View>)}
      <Pressable style={button} disabled={busy} onPress={() => void act(async () => {setReceipts((await client.walletReceipts()).receipts);})}><Text style={text}>{tr("Show receipts")}</Text></Pressable>
      {receipts.map((receipt, index) => <View key={index}>
        <Text style={text}>{receiptStatus(String(receipt.state || receipt.status || ""))} · {String(receipt.proposal_id || "")}</Text>
        <Text style={[text, {writingDirection: "ltr"}]}>{String(receipt.display_amount || "")} {String(receipt.asset || "")} · {String(receipt.network || "")}</Text>
        <Text style={[text, {writingDirection: "ltr"}]}>{String(receipt.destination || "")}</Text>
      </View>)}
    </>}
    {device?.rules?.payment_propose ? <>
      <Text style={text}>{tr("Propose payments")}</Text>
      <Text style={text}>{tr("Per-payment maximum")}: {device.payment_cap_display?.per_payment} {device.payment_cap_display?.asset} · {tr("Daily maximum")}: {device.payment_cap_display?.daily}</Text>
      <TextInput style={input} accessibilityLabel={tr("Contact or address")} placeholder={tr("Contact or address")} placeholderTextColor="#8b939e" value={destination} onChangeText={setDestination} editable={!busy && !saved}/>
      {matches.map((contact,index)=><Pressable key={index} style={button} disabled={busy || saved} onPress={()=>{setDestination(contact.display_name);setMatches([]);}}><Text style={text}>{contact.display_name}</Text></Pressable>)}
      <TextInput style={{...input,writingDirection:"ltr",textAlign:"left"}} accessibilityLabel={tr("Amount")} placeholder={tr("Amount")} placeholderTextColor="#8b939e" value={amount} onChangeText={value => setAmount(value.replace(",", "."))} keyboardType="decimal-pad" editable={!busy && !saved}/>
      {saved && <Text style={text}>{tr("Your request may already be on your computer. Check before retrying.")}</Text>}
      <Pressable style={button} disabled={busy || view?.frozen || view?.status.enabled === false} onPress={() => void act(async () => {
        await draft.send(destination, amount); setNotice(tr("Review fees and approve this payment on your computer.")); setAmount("");
      })}><Text style={text}>{saved ? tr("Retry saved proposal") : tr("Send proposal to computer")}</Text></Pressable>
      {saved && <Pressable style={button} disabled={busy} onPress={() => Alert.alert(tr("Discard saved request?"), tr("This removes the copy on this phone. It does not cancel a request on your computer."), [
        {text: tr("Cancel"), style: "cancel"}, {text: tr("Discard"), onPress: () => void act(() => draft.discard())}])}><Text style={text}>{tr("Discard saved request")}</Text></Pressable>}
      <Pressable style={button} disabled={busy} onPress={() => void act(() => client.pauseRights(["payment_propose", "payment_approve"]))}><Text style={text}>{tr("Pause payments from this phone")}</Text></Pressable>
    </> : <Text style={text}>{tr("Payment proposals are off for this phone.")}</Text>}
    <Text style={text}>{tr("Completed payments cannot be undone.")}</Text>
  </View>;
}
