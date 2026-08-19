
=============================================

User:
I just checked reconciliation for both Regular OMS and Advanced OMS
Regular OMS - as you suggested is working fine. However, when I tried reconciliation from Admin section, it showed success message stating both side matched, but the numbers it showed were completely off. Plus, the Preview does not work as well. 

I can see 43 orders on Broker's side, the same is fetched from broker's api as well, however, the application shows only 22 orders -- stating 22 checked and 22 match. 

Out of these 43, 1 was rejected and 4 were Regular OMS, it still comes down to 38 -- nowhere near 22 reported by app. Plus, I don't believe the check is done properly and I don't believe the Advanced OMS would have 100% match with broker. As you said yourself, the actual recommendation for Advanced OMS orders are done only when I initiate the same from admin section. 

I would also want a feature to add single leg entry. While setting up the program, I should have ability to state if I would want to choose leg before each cycle start. If I set that flag, the application should ask me to choose leg before initiating the order. It should also ensure the capital condition we already have in place is matched for single leg in such case and choose the strike prices based on that.

It would be great to also have 
 - a live general direction of trend for each leg. 
 - RSI direction and value (up in green arrow, down in red arrow)
 - 20 EMA and 50 EMA values (value in green if 20 EMA is higher than 50 EMA else value in red)

Example:
Capital = 5K
Capital after buffer = 4.5K
Lot Size = 65

Scenario 1:
 - Call ATM rate = 100 
 - Call ATM + 1 = 70
 - Call ATM + 2 = 50
 - Call ATM + 3 = 30

 - PUT ATM rate = 150 
 - PUT ATM - 1 = 120
 - PUT ATM - 2 = 100
 - PUT ATM - 3 = 90
 
The app should propose entries for Call ATM + 2 and no entry for Put. 

Scenario 2:
 - Call ATM rate = 100 
 - Call ATM + 1 = 70
 - Call ATM + 2 = 50
 - Call ATM + 3 = 30

 - PUT ATM rate = 50 
 - PUT ATM - 1 = 45
 - PUT ATM - 2 = 32
 - PUT ATM - 3 = 30
 
The app should propose entries for Call ATM + 2 and Put ATM. 


Scenario 3:
 - Call ATM rate = 100 
 - Call ATM + 1 = 70
 - Call ATM + 2 = 50
 - Call ATM + 3 = 30

 - PUT ATM rate = 120 
 - PUT ATM - 1 = 100
 - PUT ATM - 2 = 77
 - PUT ATM - 3 = 76.5
 
The app should propose entries for Call ATM + 2 and Put ATM + 3. 

This feature shuld be availble on both Live and Paper programs. And since multiple programs could be running at the same time, the option to select leg shoud be non-obstructive. Basically - no dialogs. Maybe CTAs on program card itself for both legs. 


Claude:





