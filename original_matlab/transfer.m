clc;    
close all;  
clear;  
% 搜寻开头
pattern = '断面编号';           %一般不变
dH = 0.1;                       %水位流量关系曲线的步长
dmsjj = ' ';                    %输入的断面数据
csj = '参数集';                 %输入的相关参数
swzb = ' ';                     %输出文件命名
swllgxqx = ' ';                 %输出文件命名
zzdxl = 0.05;                    %寻找转折点的斜率
apxl = 0.4;                     %寻找岸坡的斜率
tempjspd = 1;                   %计算方式，1为复式，0为单断面
outDir = 'RatingCurve_Pic';     %图片输出文件夹
sfsctp = 1;                     %图片输出开关
czswpd = 1;                     %成灾水位输出
xydiandao = 1;                  %XY坐标颠倒

%%-------------------------------------------------------------------------

[~, ~, RAW] = xlsread([dmsjj, '.xlsx']);
[SRrow, SRcol] = size(RAW);

[csNUM,csTXT,csRAW] = xlsread([csj, '.xlsx']);
[csSRrow, csSRcol] = size(csRAW);

RAW1=RAW;%复制专门用来搜寻

% 把第一列统一转成字符串数组
col1 = string(RAW1(:,1));

XH1idx = find(col1 == "X坐标");

% 找出以指定pattern开头的行
idx = find(startsWith(col1, pattern));

iidx = idx;

[ZBrow, ZBcol] = size(idx);

iidx(ZBrow+1,end) = SRrow+2;

% C=cell(1,ZBrow);

jjnum=1;

for i = idx'
    
    if i <= size(RAW,1)
          tempname{jjnum,1} = RAW{idx(jjnum),2};
          A(jjnum).name = RAW{idx(jjnum),2};
          A(jjnum).DX = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,1);
          A(jjnum).DY = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,2);
          if xydiandao
                A(jjnum).DX = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,2);
                A(jjnum).DY = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,1);
          else
                A(jjnum).DX = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,1);
                A(jjnum).DY = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,2);
          end
          A(jjnum).qidianju = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,3);
          A(jjnum).gaocheng = RAW(iidx(jjnum)+2:iidx(jjnum+1)-2,4);
           % ---- 取 X / Y 坐标（兼容元胞或数组）----
            DX = A(jjnum).DX;
            DY = A(jjnum).DY;

            if iscell(DX)
                DX = cell2mat(DX);
            end
            if iscell(DY)
                DY = cell2mat(DY);
            end

            % ---- 安全判断 ----
            if isempty(DX) || isempty(DY)
                A(jjnum).duanmianXY = '';
                continue
            end

            % ---- 起点、终点 ----

            x_start = DX(1);
            y_start = DY(1);

            x_end   = DX(end);
            y_end   = DY(end);
           

            % ---- 按指定格式拼接 ----
            % 格式：起点X,起点Y;终点X,终点Y
            A(jjnum).duanmianXY = sprintf('%.3f,%.3f;%.3f,%.3f', ...
                x_start, y_start, x_end, y_end);
          jjnum = jjnum + 1;
    else
        warning('idx中有超出RAW的索引：%d', i);
    end
         
    
        

end

xlswrite('断面编号参考.xlsx', tempname, 1, 'A2');%输出一下断面编号可以参考一下

% 1. 预先初始化字段，避免未匹配时报错
[A.bijiang] = deal(NaN);
[A.caolv]   = deal(NaN);
[A.Qs]      = deal(NaN);

% 2. 提取断面编号
cellName   = csRAW(:,1);     % 元胞第一列：断面编号
structName = {A.name};       % 结构体 name 字段

% 3. 建立匹配关系
[tf, loc] = ismember(cellName, structName);

% 4. 赋值
for i = 1:numel(tf)
    if tf(i)
        A(loc(i)).bijiang = csRAW{i,2};   % 第二列 → bijiang
        A(loc(i)).caolv   = csRAW{i,3};   % 第三列 → caolv
        A(loc(i)).Qs      = csRAW{i,4};   % 第四列 → Qs
    end
end


jjnum=1;

for i = idx'
    
    if i <= size(RAW,1)
                  
       
        vals = cell2mat(A(jjnum).gaocheng);            
        [A(jjnum).Dmin, A(jjnum).IdxDmin] = min(vals);

        idx01 = A(jjnum).IdxDmin;
        N = numel(vals);

        %% ----------------------
        % 左边最大值及索引
        % ----------------------
        if idx01 > 1
            [A(jjnum).Zmax, A(jjnum).IdxZmax] = max(vals(1:idx01-1));
        else
            A(jjnum).Zmax    = NaN;
            A(jjnum).IdxZmax = NaN;
        end

        %% ----------------------
        % 右边最大值及索引
        % ----------------------
        if idx01 < N
            [A(jjnum).Ymax, idx_tmp] = max(vals(idx01+1:end));
            A(jjnum).IdxYmax = idx_tmp + idx01;   % 索引平移
        else
            A(jjnum).Ymax    = NaN;
            A(jjnum).IdxYmax = NaN;
        end

        
        A(jjnum).ZYmin = min([A(jjnum).Zmax, A(jjnum).Ymax]);%找到左右最大值中的最小值
        
        sec = [cell2mat(A(jjnum).qidianju) cell2mat(A(jjnum).gaocheng)];
        x = sec(:,1);
        z = sec(:,2);
        N = length(x);
        
        %% ---------左右岸转折点判定---------------------
        % 断面数据准备
        % ----------------------------------------------------
        zzzd  = cell2mat(A(jjnum).gaocheng);     
        xzzd  = cell2mat(A(jjnum).qidianju);     

        k0zzd   = A(jjnum).IdxDmin;               
        nptzzd  = length(zzzd);

        % 初始化
        IdxLeftTurn  = NaN;
        IdxRightTurn = NaN;

        % ==============================
        % 向左搜索
        % ==============================
        passedSteep = false;

        for kzzd = k0zzd-1:-1:1

            dxzzd = xzzd(kzzd+1) - xzzd(kzzd);
            if dxzzd <= 0
                continue
            end

            dzzzd = zzzd(kzzd) - zzzd(kzzd+1);
            slopezd = dzzzd / dxzzd;

            % ---------- 第一阶段：寻找陡坡 ----------
            if ~passedSteep
                if slopezd > apxl
                    passedSteep = true;
                end
                continue
            end

            % ---------- 第二阶段：寻找转折 ----------
            if dzzzd < 0 || slopezd < zzdxl
                IdxLeftTurn = kzzd+1;
                break
            end

        end

        % ==============================
        % 向右搜索
        % ==============================
        passedSteep = false;

        for kzzd = k0zzd+1:nptzzd

            dxzzd = xzzd(kzzd) - xzzd(kzzd-1);
            if dxzzd <= 0
                continue
            end

            dzzzd = zzzd(kzzd) - zzzd(kzzd-1);
            slopezd = dzzzd / dxzzd;

            if ~passedSteep
                if slopezd > apxl
                    passedSteep = true;
                end
                continue
            end

            if dzzzd < 0 || slopezd < zzdxl
                IdxRightTurn = kzzd-1;
                break
            end

        end

        % ------------------------------
        % 写入结构体
        % ------------------------------
           
        A(jjnum).IdxLeftTurn  = IdxLeftTurn;
    
        A(jjnum).IdxRightTurn = IdxRightTurn;
        
        %% 保存成灾水位及索引坐标
        if czswpd
            IdxL = A(jjnum).IdxLeftTurn;
            IdxR = A(jjnum).IdxRightTurn;

            % ---- 左右转折点承载水位判定 ----
            if isnan(IdxL) && isnan(IdxR)
                % 左右都没找到（放入断面最低水位）
                A(jjnum).ChengZaiShuiWei = A(jjnum).Dmin;
                A(jjnum).IdxChengZaiShuiWei = A(jjnum).IdxDmin;

            elseif isnan(IdxL)
                % 只有右侧存在
                A(jjnum).ChengZaiShuiWei = zzzd(IdxR);
                A(jjnum).IdxChengZaiShuiWei = IdxR;

            elseif isnan(IdxR)
                % 只有左侧存在
                A(jjnum).ChengZaiShuiWei = zzzd(IdxL);
                A(jjnum).IdxChengZaiShuiWei = IdxL;

            else
                
                if zzzd(IdxL) <= zzzd(IdxR)
                    A(jjnum).ChengZaiShuiWei    = zzzd(IdxL);
                    A(jjnum).IdxChengZaiShuiWei = IdxL;
                else
                    A(jjnum).ChengZaiShuiWei    = zzzd(IdxR);
                    A(jjnum).IdxChengZaiShuiWei = IdxR;
                end
                
            end

        end
        
        %% 分区
        idxList = [];

        if ~isnan(A(jjnum).IdxLeftTurn)
            idxList = [idxList, A(jjnum).IdxLeftTurn];
        end

        if ~isnan(A(jjnum).IdxRightTurn)
            idxList = [idxList, A(jjnum).IdxRightTurn];
        end

        idxList = sort(idxList);

        % 生成分区范围
        if isempty(idxList)
            zones = {1:nptzzd};                       % 单断面
        elseif length(idxList)==1
            zones = {1:idxList(1), idxList(1):nptzzd};% 双断面
        else
            zones = {1:idxList(1), ...
                     idxList(1):idxList(2), ...
                     idxList(2):nptzzd};              % 复式断面
        end
%% ---------水位流量关系曲线计算-------------------------

        if tempjspd

            A(jjnum).Hvec = (A(jjnum).Dmin:dH:A(jjnum).ZYmin)';  % 水位范围
            Hxr = A(jjnum).Hvec;

            nH = length(Hxr);

            Qxr = zeros(nH,1);
            Axr = zeros(nH,1);
            Pxr = zeros(nH,1);
            Bxr = zeros(nH,1);

            S = A(jjnum).bijiang;

            for ih = 1:nH

                Hnow = Hxr(ih);

                Qtot = 0;
                Atot = 0;
                Ptot = 0;
                Btot = 0;

                % ===== 遍历每个子断面 =====
                for iz = 1:length(zones)

                    idx_zdm = zones{iz};
                    xsub = xzzd(idx_zdm);
                    zsub = zzzd(idx_zdm);

                    % -------- 计算该子断面几何量 --------
                    [Asub, Psub, Bsub] = sectionGeom(xsub, zsub, Hnow);

                    if Asub <= 0
                        continue
                    end

                    Rsub = Asub / Psub;

                    % 糙率（可不同，也可统一）
                    if isfield(A(jjnum),'caolv_sub')
                        nsub = A(jjnum).caolv_sub(iz);
                    else
                        nsub = A(jjnum).caolv;
                    end

                    Qsub = (1/nsub) * Asub * Rsub^(2/3) * sqrt(S);

                    Qtot = Qtot + Qsub;
                    Atot = Atot + Asub;
                    Ptot = Ptot + Psub;
                    Btot = Btot + Bsub;

                end

                Qxr(ih) = Qtot;
                Axr(ih) = Atot;
                Pxr(ih) = Ptot;
                Bxr(ih) = Btot;

            end

            % 写回结构体
            A(jjnum).Qvec = Qxr;
            A(jjnum).Avec = Axr;
            A(jjnum).Pvec = Pxr;
            A(jjnum).Bvec = Bxr;
        else
            n = A(jjnum).caolv;               % 曼宁糙率
            S = A(jjnum).bijiang;          % 坡降（‰ -> 换算为无量纲）
            A(jjnum).Hvec = (A(jjnum).Dmin:dH:A(jjnum).ZYmin)';  % 水位范围
            Hvec = A(jjnum).Hvec;


            % 预分配
            Qvec = zeros(size(Hvec));
            Avec = zeros(size(Hvec));
            Pvec = zeros(size(Hvec));
            Bvec = zeros(size(Hvec));  % 顶宽（水面宽度）


            % ------------------------------
            % 循环计算每个水位下的A, P, B, Q
            % ------------------------------
            for k = 1:length(Hvec)
                H = Hvec(k);
                As = 0;
                P = 0;
                x_left = inf;
                x_right = -inf;

                for j = 1:N-1
                    x1 = x(j); z1 = z(j);
                    x2 = x(j+1); z2 = z(j+1);

                    % 若线段两端都在水位以下（或等于）
                    if z1 <= H && z2 <= H
                        dx = x2 - x1;
                        % 面积（床面到水位的梯形）
                        As = As + ((H - z1) + (H - z2))/2 * dx;
                        % 湿周加床面长度
                        P = P + sqrt((x2 - x1)^2 + (z2 - z1)^2);
                        % 更新水面左右界（端点在水下）
                        x_left = min(x_left, x1);
                        x_right = max(x_right, x2);

                    % 若线段跨越水位（有一端在水下，一端在水上）
                    elseif (z1 <= H && z2 > H) || (z1 > H && z2 <= H)
                        t = (H - z1) / (z2 - z1);      % 线性插值参数
                        xi = x1 + t * (x2 - x1);       % 交点横坐标 (y = H)
                        % 记录交点为水面边界
                        x_left = min(x_left, xi);
                        x_right = max(x_right, xi);

                        if z1 <= H && z2 > H
                            % 湿润部分为 x1 -> xi
                            dx = xi - x1;
                            As = As + ((H - z1) + 0)/2 * dx;
                            P = P + sqrt((xi - x1)^2 + (H - z1)^2);
                        else
                            % z1 > H && z2 <= H 湿润部分为 xi -> x2
                            dx = x2 - xi;
                            As = As + (0 + (H - z2))/2 * dx;
                            P = P + sqrt((x2 - xi)^2 + (H - z2)^2);
                        end
                    end
                    % 其它情况（两端都高于水位）对当前段无贡献
                end

                % 如果没有湿润区域，则 Q=0
                if isinf(x_left) || isinf(x_right) || As <= 0 || P <= 0
                    Avec(k) = 0;
                    Pvec(k) = 0;
                    Bvec(k) = 0;
                    Qvec(k) = 0;
                    continue;
                end

                % 顶宽
                B = x_right - x_left;
                R = As / P;  % 水力半径

                % 曼宁公式
                Q = (1 / n) * As * R^(2/3) * sqrt(S);

                % 存储
                Avec(k) = As;
                Pvec(k) = P;
                Bvec(k) = B;
                Qvec(k) = Q;
            end
            A(jjnum).Qvec = Qvec;
            A(jjnum).Avec = Avec;
            A(jjnum).Pvec = Pvec;
            A(jjnum).Bvec = Bvec;
            
        end


       
  %% 插值得到设计流量对应的水位高程
       A(jjnum).Hs = interp1( ...
        A(jjnum).Qvec, ...
        A(jjnum).Hvec, ...
        A(jjnum).Qs, ...
        'linear', 'extrap' ...
        );
       A(jjnum).ChengZaiLiuLiang = interp1(A(jjnum).Hvec, A(jjnum).Qvec, A(jjnum).ChengZaiShuiWei, 'linear', 'extrap');
       A(jjnum).Hs1 = A(jjnum).Hs + 1;%百年一遇加一米
%% 找到插值位置
        zcz = cell2mat(A(jjnum).gaocheng);   % 若本身是数值向量，可直接用
        Hs = A(jjnum).Hs1;
        idx0 = A(jjnum).IdxDmin;
        Nz = length(zcz);

        % ----------------------
        % 左侧搜索
        % ----------------------
        IdxZcz1 = NaN;
        IdxZcz2 = NaN;

        for i = idx0:-1:2
            if zcz(i) <= Hs && zcz(i-1) > Hs
                IdxZcz1 = i;
                IdxZcz2 = i-1;
                break
            end
        end

        % ----------------------
        % 右侧搜索
        % ----------------------
        IdxYcz1 = NaN;
        IdxYcz2 = NaN;

        for i = idx0: Nz-1
            if zcz(i) <= Hs && zcz(i+1) > Hs
                IdxYcz1 = i;
                IdxYcz2 = i+1;
                break
            end
        end

        % ----------------------
        % 存入结构体
        % ----------------------
        A(jjnum).IdxZcz1 = IdxZcz1;
        A(jjnum).IdxZcz2 = IdxZcz2;

        A(jjnum).IdxYcz1 = IdxYcz1;
        A(jjnum).IdxYcz2 = IdxYcz2;
        %% ----------------------
        % 左侧插值索引是否有效
        % ----------------------
        if ~isnan(A(jjnum).IdxZcz1) && ~isnan(A(jjnum).IdxZcz2)
            A(jjnum).Zbs = 156;   % 找到左侧插值位置
        else
            A(jjnum).Zbs = 172;   % 未找到左侧插值位置
        end

        %% ----------------------
        % 右侧插值索引是否有效
        % ----------------------
        if ~isnan(A(jjnum).IdxYcz1) && ~isnan(A(jjnum).IdxYcz2)
            A(jjnum).Ybs = 156;   % 找到右侧插值位置
        else
            A(jjnum).Ybs = 172;   % 未找到右侧插值位置
        end
        %% 预分配
        Hs = A(jjnum).Hs1;

        z  = cell2mat(A(jjnum).gaocheng);
        x  = cell2mat(A(jjnum).DX);
        y  = cell2mat(A(jjnum).DY);

        %% ==================================================
        % 左岸水面交点
        if A(jjnum).Zbs == 156
            % 插值索引
            i1 = A(jjnum).IdxZcz1;
            i2 = A(jjnum).IdxZcz2;

            z1 = z(i1);  z2 = z(i2);
            x1 = x(i1);  x2 = x(i2);
            y1 = y(i1);  y2 = y(i2);

            % 线性插值比例
            t = (Hs - z1) / (z2 - z1);

            % 水面交点坐标
            A(jjnum).DXsz = x1 + t * (x2 - x1);
            A(jjnum).DYsz = y1 + t * (y2 - y1);

        else
            % 未找到插值位置，取左岸最大值点
            idx = A(jjnum).IdxZmax;

            A(jjnum).DXsz = x(idx);
            A(jjnum).DYsz = y(idx);
        end

        %% ==================================================
        % 右岸水面交点
        if A(jjnum).Ybs == 156
            % 插值索引
            i1 = A(jjnum).IdxYcz1;
            i2 = A(jjnum).IdxYcz2;

            z1 = z(i1);  z2 = z(i2);
            x1 = x(i1);  x2 = x(i2);
            y1 = y(i1);  y2 = y(i2);

            % 线性插值比例
            t = (Hs - z1) / (z2 - z1);

            % 水面交点坐标
            A(jjnum).DXsy = x1 + t * (x2 - x1);
            A(jjnum).DYsy = y1 + t * (y2 - y1);

        else
            % 未找到插值位置，取右岸最大值点
            idx = A(jjnum).IdxYmax;

            A(jjnum).DXsy = x(idx);
            A(jjnum).DYsy = y(idx);
        end
        
        jjnum = jjnum + 1;
    else
        warning('idx中有超出RAW的索引：%d', i);
    end
    
end

nSec = numel(A);

% 预分配：每个断面 2 行
if czswpd
    outCell = cell(nSec*3 + 1, 4);
    row = 3;
else
   outCell = cell(nSec*2 + 1, 4);
    row = 2; 
end

for jjnum = 1:nSec
    secName = A(jjnum).name;

    %% -------- 左岸 Z --------
    outCell{row,1} = [secName 'Z'];
    outCell{row,2} = A(jjnum).DXsz;
    outCell{row,3} = A(jjnum).DYsz;
    outCell{row,4} = A(jjnum).Zbs;
    row = row + 1;

    %% -------- 右岸 Y --------
    outCell{row,1} = [secName 'Y'];
    outCell{row,2} = A(jjnum).DXsy;
    outCell{row,3} = A(jjnum).DYsy;
    outCell{row,4} = A(jjnum).Ybs;
    row = row + 1;
    
    %% ------ 成灾水位 H -------
    if czswpd
        outCell{row,1} = [secName '成灾水位:' num2str(A(jjnum).ChengZaiShuiWei) '成灾流量:' num2str(A(jjnum).ChengZaiLiuLiang)];
        outCell{row,2} = cell2mat(A(jjnum).DX(A(jjnum).IdxChengZaiShuiWei));
        outCell{row,3} = cell2mat(A(jjnum).DY(A(jjnum).IdxChengZaiShuiWei));
        outCell{row,4} = 152;
        row = row + 1;
    end
    end

swfilename = [swzb, '.csv'];  % 拼接文件名
fid = fopen(swfilename, 'w'); % 打开文件

% 写表头
fprintf(fid, '名称,平面坐标X,平面坐标Y,图标样式\n');

% 写数据
for i = 2:size(outCell,1)
    fprintf(fid, '%s,%.6f,%.6f,%d\n', ...
        outCell{i,1}, outCell{i,2}, outCell{i,3}, outCell{i,4});
end

fclose(fid);

swllfilename = [swllgxqx, '.csv'];  % 拼接文件名
fid = fopen(swllfilename, 'w');     % 打开文件

for jj = 1:numel(A)

    fprintf(fid,'断面=%s\n',A(jj).name);
    fprintf(fid,'水位/m,流量/m3/s,面积/m2,湿周,顶宽/m\n');

    Hxr = A(jj).Hvec(:);
    Qxr = A(jj).Qvec(:);
    Axr = A(jj).Avec(:);
    Pxr = A(jj).Pvec(:);
    Bxr = A(jj).Bvec(:);

    for i = 1:length(Hxr)
        fprintf(fid,'%.6f,%.6f,%.6f,%.6f,%.6f\n', ...
                Hxr(i),Qxr(i),Axr(i),Pxr(i),Bxr(i));
    end

    fprintf(fid,'\n');   % 空行分隔不同断面
end

fclose(fid);

%% 二、组织 CSV 输出内容

nSec = length(A);

outCell = cell(nSec+1, 3);

% 表头
outCell(1,:) = {'名称', '平面坐标[X+Y]','百年一遇水位（m）'};

% 数据
for jjnum = 1:nSec
    outCell{jjnum+1, 1} = A(jjnum).name;
    outCell{jjnum+1, 2} = A(jjnum).duanmianXY;
    outCell{jjnum+1, 3} = A(jjnum).Hs;
end

% 三、写入 CSV 文件（高速方式）
csvFile = '断面起终点坐标及水位.csv';
fid = fopen(csvFile, 'w');

% 写表头
fprintf(fid, '%s,%s,%s\n', outCell{1,1}, outCell{1,2}, outCell{1,3});

% 写数据（关键在这里）
for i = 2:size(outCell,1)
    fprintf(fid, '%s,"%s",%s\n', outCell{i,1}, outCell{i,2}, outCell{i,3});
end

fclose(fid);

%% 输出图像
if sfsctp
    
    if ~exist(outDir,'dir')
        mkdir(outDir)
    end

    for jjnum = 1:length(A)

        % ===== 取数据 =====
        Qxrzt = A(jjnum).Qvec;
        Hxrzt = A(jjnum).Hvec;

        Qs  = A(jjnum).Qs;     % 设计流量
        Hs  = A(jjnum).Hs;     % 设计水位

        % ===== 绘图 =====
        figure('Color','w','Visible','off')   % off可加快批量出图

        plot(Qxrzt, Hxrzt,'LineWidth',1.8)
        hold on
        grid on
        box on

        % ——设计点标注——
        plot(Qs, Hs,'ro','MarkerSize',8,'LineWidth',2)
        text(Qs, Hs, sprintf('  Q=%.1fm^3/s  H=%.2fm',Qs,Hs), ...
             'FontSize',10,'Color','r')

        % ——坐标及标题——
        xlabel('流量 Q (m^3/s)','FontSize',11)
        ylabel('水位 H (m)','FontSize',11)
        title(['断面 ',A(jjnum).name,' 水位-流量关系曲线'])

        set(gca,'FontSize',10)

        % ===== 自动保存 =====
        picName = fullfile(outDir, ...
                  ['Rating_',A(jjnum).name,'.png']);

        print(picName,'-dpng','-r300')   % 高清工程输出

        close   % 关闭窗口防止内存堆积

    end
end

